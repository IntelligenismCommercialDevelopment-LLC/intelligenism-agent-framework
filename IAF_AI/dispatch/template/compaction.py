"""
Compaction — Context compression for Long Running Dispatch.

Implements the threshold trigger, exempt-zone calculation, and
merge-recompress strategy via V4 Processor.

Public API:
    check_and_trigger(sid, config, next_user_message="") -> CompactionResult
    force_compact(sid, config) -> CompactionResult

The module is stateless across calls; all state lives in
sessions/{sid}/staging/compacted_history.md and history.jsonl.

Thread-safety: uses a POSIX file lock at sessions/{sid}/.compaction.lock.
"""

import fcntl
import os
import sys
import time
from dataclasses import dataclass, field

_DISPATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_DISPATCH_DIR))
if _DISPATCH_DIR not in sys.path:
    sys.path.insert(0, _DISPATCH_DIR)

import session_io
import v4_processor


# ---------------------------------------------------------------------------
# Result struct
# ---------------------------------------------------------------------------

@dataclass
class CompactionResult:
    compacted: bool = False
    tokens_before: int = 0
    tokens_after: int = 0
    compacted_rounds: list = field(default_factory=list)
    file_version: int = 0
    duration_ms: int = 0
    error: str = ""


# ---------------------------------------------------------------------------
# Token estimation (reuse lib.token_utils if importable)
# ---------------------------------------------------------------------------

def _estimate_tokens(text):
    try:
        lib_path = os.path.join(_PROJECT_ROOT, "lib")
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        from token_utils import estimate_tokens
        return estimate_tokens(text)
    except ImportError:
        return max(1, len(text or "") // 4)


# ---------------------------------------------------------------------------
# File lock (POSIX)
# ---------------------------------------------------------------------------

def _lock_path(sid):
    return os.path.join(session_io.session_dir(_DISPATCH_DIR, sid), ".compaction.lock")


def _acquire_lock(sid, timeout_sec=30):
    path = _lock_path(sid)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o644)
    t0 = time.time()
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.time() - t0 > timeout_sec:
                os.close(fd)
                raise TimeoutError(f"compaction lock timeout for {sid}")
            time.sleep(0.2)


def _release_lock(fd):
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Context file reading (mirrors dispatch._read_context_file logic)
# ---------------------------------------------------------------------------

def _resolve_context_path(path, sid):
    if path.startswith("session:"):
        if not sid:
            return None
        rel = path[len("session:"):]
        return os.path.join(session_io.session_dir(_DISPATCH_DIR, sid), rel)
    return os.path.join(_DISPATCH_DIR, path)


def _read_file_safe(path):
    if path is None:
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IOError):
        return ""


# ---------------------------------------------------------------------------
# Total token measurement
# ---------------------------------------------------------------------------

def _measure_total_tokens(sid, config, next_user_message=""):
    """Estimate tokens that WOULD be sent in the next LLM call.

    Includes:
      - All context_files content (prompt + compacted_history + working_notes)
      - All non-compacted rounds from history.jsonl
      - The next_user_message (if provided)
    """
    core_agent_name = config.get("core_agent", "runner")
    agent_conf = config["agents"][core_agent_name]
    context_files = agent_conf.get("context_files", [])

    total = 0
    # System prompt parts
    for cf in context_files:
        content = _read_file_safe(_resolve_context_path(cf, sid))
        total += _estimate_tokens(content)

    # History (only non-compacted rounds — same filter as dispatch._load_conversation_history)
    records = session_io.load_history(_DISPATCH_DIR, sid)
    compacted = set()
    for rec in records:
        if rec.get("type") == "trimmed_marker":
            for r in rec.get("compacted_rounds", []):
                compacted.add(r)

    round_num = 0
    conv = [r for r in records if r.get("type") in ("user_input", "agent_response")]
    i = 0
    while i < len(conv):
        rec = conv[i]
        if rec.get("type") == "user_input":
            round_num += 1
            if round_num in compacted:
                i += 1
                if i < len(conv) and conv[i].get("type") == "agent_response":
                    i += 1
                continue
            total += _estimate_tokens(rec.get("content", ""))
            if i+1 < len(conv) and conv[i+1].get("type") == "agent_response":
                total += _estimate_tokens(conv[i+1].get("content", ""))
                i += 2
            else:
                i += 1
        else:
            i += 1

    # Next user message
    total += _estimate_tokens(next_user_message)

    return total


# ---------------------------------------------------------------------------
# Round pairing helper
# ---------------------------------------------------------------------------

def _pair_rounds(records):
    """Return list of rounds, each being a dict:
        {'round_num': N, 'user': str, 'agent': str, 'user_idx': i, 'agent_idx': j}
    Rounds are numbered by order of appearance of user_input records."""
    rounds = []
    round_num = 0
    i = 0
    conv = [(idx, r) for idx, r in enumerate(records)
            if r.get("type") in ("user_input", "agent_response")]
    while i < len(conv):
        idx, rec = conv[i]
        if rec.get("type") == "user_input":
            round_num += 1
            entry = {
                "round_num": round_num,
                "user": rec.get("content", ""),
                "agent": "",
                "user_idx": idx,
                "agent_idx": None,
            }
            if i+1 < len(conv) and conv[i+1][1].get("type") == "agent_response":
                entry["agent"] = conv[i+1][1].get("content", "")
                entry["agent_idx"] = conv[i+1][0]
                i += 2
            else:
                i += 1
            rounds.append(entry)
        else:
            i += 1
    return rounds


# ---------------------------------------------------------------------------
# Exempt zone calculation
# ---------------------------------------------------------------------------

def _compute_exempt_rounds(rounds, compacted_set, min_rounds, min_tokens):
    """Returns (exempt_round_nums, exempt_tokens).
    exempt_round_nums is a set of round numbers that should NOT be compacted."""
    # Only consider rounds that are not already compacted
    eligible = [r for r in rounds if r["round_num"] not in compacted_set]

    exempt = set()
    tail_tokens = 0
    tail_count = 0
    for r in reversed(eligible):
        r_tokens = _estimate_tokens(r["user"]) + _estimate_tokens(r["agent"])
        exempt.add(r["round_num"])
        tail_tokens += r_tokens
        tail_count += 1
        if tail_count >= min_rounds and tail_tokens >= min_tokens:
            break

    return exempt, tail_tokens


# ---------------------------------------------------------------------------
# V4 Processor input assembly
# ---------------------------------------------------------------------------

def _build_v4_input(sid, rounds_to_compact):
    """Build the raw_text input for v4_processor.process().

    Format:
        ## Previous Compacted Context
        {existing compacted_history.md content or 'No previous compaction.'}

        ---

        ## New Rounds Since Last Compaction
        Round N:
          User: ...
          Agent: ...
    """
    old_compacted_path = os.path.join(
        session_io.session_dir(_DISPATCH_DIR, sid),
        "staging", "compacted_history.md"
    )
    old_compacted = _read_file_safe(old_compacted_path).strip()
    if not old_compacted:
        old_section = "No previous compaction."
    else:
        old_section = old_compacted

    parts = [
        "## Previous Compacted Context",
        "",
        old_section,
        "",
        "---",
        "",
        "## New Rounds Since Last Compaction",
        "",
    ]
    for r in rounds_to_compact:
        parts.append(f"Round {r['round_num']}:")
        parts.append(f"  User: {r['user']}")
        parts.append(f"  Agent: {r['agent']}")
        parts.append("")

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Main compaction procedure
# ---------------------------------------------------------------------------

def _do_compact(sid, config):
    """Perform the actual compaction. Assumes lock is held.
    Returns CompactionResult (partial — caller fills tokens_before)."""
    result = CompactionResult()
    t0 = time.time()

    comp_cfg = config.get("compaction", {})
    min_rounds = int(comp_cfg.get("exempt_min_rounds", 3))
    min_tokens = int(comp_cfg.get("exempt_min_tokens", 6000))
    output_rel = comp_cfg.get("output_file", "session:staging/compacted_history.md")

    rules_file = config.get("v4_rules", {}).get("compact", "rules/compact_v4.md")

    v4_cfg = config.get("agents", {}).get("v4_processor", {})
    provider = v4_cfg.get("provider", "")
    model = v4_cfg.get("model", "")
    temperature = v4_cfg.get("temperature")
    top_p = v4_cfg.get("top_p")

    # --- Load history, identify compacted and exempt rounds ---
    records = session_io.load_history(_DISPATCH_DIR, sid)
    compacted_set = set()
    current_version = 0
    for rec in records:
        if rec.get("type") == "trimmed_marker":
            for r in rec.get("compacted_rounds", []):
                compacted_set.add(r)
            current_version = max(current_version, int(rec.get("compacted_file_version", 0)))

    rounds = _pair_rounds(records)
    exempt_set, _ = _compute_exempt_rounds(rounds, compacted_set, min_rounds, min_tokens)

    # Rounds to compact = not already compacted AND not exempt
    to_compact = [r for r in rounds
                  if r["round_num"] not in compacted_set and r["round_num"] not in exempt_set]

    if not to_compact:
        result.error = "nothing_to_compact_exempt_too_large"
        result.duration_ms = int((time.time() - t0) * 1000)
        return result

    # --- Call V4 Processor ---
    print(f"[compaction] started: {len(to_compact)} rounds to compress")

    v4_input = _build_v4_input(sid, to_compact)
    v4_output = v4_processor.process(
        raw_text=v4_input,
        rules_file=rules_file,
        provider=provider,
        model=model,
        temperature=temperature,
        top_p=top_p,
    )

    if v4_output.startswith("[V4 Processor Error]"):
        result.error = v4_output
        result.duration_ms = int((time.time() - t0) * 1000)
        print(f"[compaction] failed: {v4_output[:200]}")
        return result

    # --- Write new compacted_history.md ---
    out_abs = _resolve_context_path(output_rel, sid)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    with open(out_abs, "w", encoding="utf-8") as f:
        f.write(v4_output)

    # --- Append trimmed_marker to history ---
    compacted_round_nums = [r["round_num"] for r in to_compact]
    new_version = current_version + 1
    session_io.append_history(_DISPATCH_DIR, sid, {
        "type": "trimmed_marker",
        "compacted_rounds": compacted_round_nums,
        "compacted_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "compacted_file_version": new_version,
    })

    result.compacted = True
    result.compacted_rounds = compacted_round_nums
    result.file_version = new_version
    result.tokens_after = _estimate_tokens(v4_output)
    result.duration_ms = int((time.time() - t0) * 1000)

    print(f"[compaction] done: v{new_version}, {len(compacted_round_nums)} rounds compressed, "
          f"{result.duration_ms}ms")

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_and_trigger(sid, config, next_user_message=""):
    """
    Check threshold and trigger compaction if necessary.

    Returns CompactionResult.
    """
    result = CompactionResult()
    comp_cfg = config.get("compaction", {})

    if not comp_cfg.get("enabled", False):
        return result

    trig_th = int(comp_cfg.get("trigger_threshold_tokens", 100000))

    total = _measure_total_tokens(sid, config, next_user_message)
    result.tokens_before = total

    if total >= trig_th:
        # Need to compact
        try:
            fd = _acquire_lock(sid)
        except TimeoutError as e:
            result.error = str(e)
            return result
        try:
            # Re-measure inside the lock (another thread may have compacted)
            total_locked = _measure_total_tokens(sid, config, next_user_message)
            result.tokens_before = total_locked
            if total_locked < trig_th:
                # Someone else already handled it; no-op
                return result
            sub = _do_compact(sid, config)
            result.compacted = sub.compacted
            result.tokens_after = sub.tokens_after
            result.compacted_rounds = sub.compacted_rounds
            result.file_version = sub.file_version
            result.duration_ms = sub.duration_ms
            result.error = sub.error
        finally:
            _release_lock(fd)

    return result


def force_compact(sid, config, _skip_lock=False):
    """Manual compaction entry. Always attempts compaction regardless of
    thresholds. _skip_lock: if True, caller already holds the session lock."""
    result = CompactionResult()
    fd = None
    if not _skip_lock:
        try:
            fd = _acquire_lock(sid)
        except TimeoutError as e:
            result.error = str(e)
            return result
    try:
        result.tokens_before = _measure_total_tokens(sid, config, next_user_message="")
        sub = _do_compact(sid, config)
        result.compacted = sub.compacted
        result.tokens_after = sub.tokens_after
        result.compacted_rounds = sub.compacted_rounds
        result.file_version = sub.file_version
        result.duration_ms = sub.duration_ms
        result.error = sub.error
    finally:
        if fd is not None:
            _release_lock(fd)
    return result
