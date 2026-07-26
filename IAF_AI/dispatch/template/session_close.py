"""
Session Close — Memory update workflow for Chat Dispatch.

On session close, updates the agent's personal memory files
(experiences, relationships, emotional_state) via LLM calls.

Public API:
    close(sid, project_root) -> generator of progress events

Each yielded event has shape:
    {"event": "session_close_progress", "step": N, "step_name": "...",
     "status": "running|ok|skipped|fallback|failed",
     "detail": "...", "timestamp": "..."}

The final event is:
    {"event": "session_closed", "duration_ms": N}
"""

import json
import os
import sys
import time

_DISPATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_DISPATCH_DIR))

if _DISPATCH_DIR not in sys.path:
    sys.path.insert(0, _DISPATCH_DIR)

import session_io
from dispatch_base import load_global_config, resolve_llm_endpoint


def _load_config():
    """Load dispatch_config.json from this dispatch directory."""
    config_path = os.path.join(_DISPATCH_DIR, "dispatch_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _progress(step, step_name, status, detail="", **extra):
    evt = {
        "event": "session_close_progress",
        "step": step,
        "step_name": step_name,
        "status": status,
        "detail": detail,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    evt.update(extra)
    return evt


def _estimate_tokens(text):
    try:
        lib_path = os.path.join(_PROJECT_ROOT, "lib")
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        from token_utils import estimate_tokens
        return estimate_tokens(text)
    except ImportError:
        return max(1, len(text or "") // 4)


def _read_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except (FileNotFoundError, IOError):
        return ""


# ---------------------------------------------------------------------------
# LLM call (inlined from deleted v4_processor.py)
# ---------------------------------------------------------------------------

def _call_llm_single(raw_text, rules_file, provider, model):
    """
    Single LLM call: rules_file as system prompt, raw_text as user message.
    Returns output text, or a string starting with "[Error]" on failure.
    """
    if not raw_text or not raw_text.strip():
        return "[Error] Empty input"

    global_config = load_global_config(_PROJECT_ROOT)

    if not provider:
        provider = global_config.get("default_provider", "")
    if not model:
        model = global_config.get("default_model", "")

    url, key = resolve_llm_endpoint(provider, global_config)
    if not url or not key:
        return f"[Error] No endpoint for provider={provider}"

    # Load rules as system prompt
    if os.path.isabs(rules_file):
        rules_path = rules_file
    else:
        rules_path = os.path.join(_DISPATCH_DIR, rules_file)

    system_prompt = _read_safe(rules_path).strip()
    if not system_prompt:
        system_prompt = f"(rules file not found: {rules_file})"

    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user",   "content": raw_text},
    ]

    try:
        lib_path = os.path.join(_PROJECT_ROOT, "lib")
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        from llm_client import call_llm
    except ImportError as e:
        return f"[Error] llm_client not importable: {e}"

    try:
        response = call_llm(url, key, model, messages)
    except Exception as e:
        return f"[Error] LLM call failed: {e}"

    if isinstance(response, dict):
        text = response.get("content", "") or ""
    elif isinstance(response, str):
        text = response
    else:
        text = str(response)

    text = text.strip()
    if not text:
        return "[Error] LLM returned empty content"

    return text


# ---------------------------------------------------------------------------
# Path resolution for context_sources
# ---------------------------------------------------------------------------

def _resolve_source_path(source, sid, agent_id):
    """
    Resolve a context_sources entry to an absolute file path.

    Prefixes:
      - "session:"  -> sessions/{sid}/<rest>
      - "briefs:"   -> {project_root}/briefs/<rest>
      - "wiki:"     -> {wiki_root}/<rest>
      - plain path  -> relative to dispatch directory
    """
    source = source.replace("{agent_id}", agent_id)

    if source.startswith("session:"):
        rel = source[len("session:"):]
        return os.path.join(session_io.session_dir(_DISPATCH_DIR, sid), rel)
    elif source.startswith("briefs:"):
        filename = source[len("briefs:"):]
        return os.path.join(_PROJECT_ROOT, "briefs", filename)
    elif source.startswith("wiki:"):
        rel = source[len("wiki:"):]
        wiki_root = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(_PROJECT_ROOT))), "wiki")
        return os.path.join(wiki_root, rel)
    else:
        return os.path.join(_DISPATCH_DIR, source)


def _read_context_sources(sources, sid, agent_id):
    """
    Read all context_sources and return a list of (label, content) tuples.
    Skips files that don't exist or are empty.
    If a source resolves to a directory, all .md files inside are expanded.
    """
    result = []
    for source in sources:
        path = _resolve_source_path(source, sid, agent_id)

        if os.path.isdir(path):
            for filename in sorted(os.listdir(path)):
                if filename.endswith(".md") and not filename.startswith("."):
                    content = _read_safe(os.path.join(path, filename))
                    if content.strip():
                        result.append((f"{source}/{filename}", content))
            continue

        content = _read_safe(path)
        if content.strip():
            label = source.replace("{agent_id}", agent_id)
            result.append((label, content))
    return result


# ---------------------------------------------------------------------------
# Memory update step
# ---------------------------------------------------------------------------

def _step_update_personal_memory_file(sid, config, file_type):
    """
    Update one personal memory file (experiences / relationships / emotional_state).

    Reads context_sources, applies rules via LLM, writes result to output_target.
    """
    agent_id = config.get("agent_id", "unknown")

    writer_key = f"memory_{file_type}_writer"
    writer_cfg = config.get("agents", {}).get(writer_key, {})

    provider = writer_cfg.get("provider", "")
    model = writer_cfg.get("model", "")
    rules = writer_cfg.get("rules_file", "")
    context_sources = writer_cfg.get("context_sources", [])
    output_target = writer_cfg.get("output_target", "")

    if not rules or not context_sources:
        return "skipped", f"{writer_key} not configured"

    # Ensure memory file exists
    output_path = _resolve_source_path(output_target, sid, agent_id)
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    if not os.path.exists(output_path):
        with open(output_path, "w", encoding="utf-8") as f:
            f.write("")

    # Read context
    source_data = _read_context_sources(context_sources, sid, agent_id)
    if not source_data:
        return "skipped", "no context data available"

    parts = []
    for label, content in source_data:
        parts.append(f"## {label}\n\n{content}")
    raw_input = "\n\n---\n\n".join(parts)

    # Resolve rules_file path
    rules_path = _resolve_source_path(rules, sid, agent_id)

    # Call LLM
    try:
        out = _call_llm_single(
            raw_text=raw_input,
            rules_file=rules_path,
            provider=provider,
            model=model,
        )
    except Exception as e:
        return "fallback", f"exception: {e}"

    if out.startswith("[Error]"):
        return "fallback", f"llm failed: {out[:100]}"

    # Write output (LLM output = full file content)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(out)

    tokens = _estimate_tokens(out)
    return "ok", f"{tokens} tokens"


# ---------------------------------------------------------------------------
# Main entry
# ---------------------------------------------------------------------------

def close(sid, project_root, closed_by="user_button"):
    """
    Execute session close: update personal memory files.
    Yields progress events.
    """
    close_start = time.time()

    if not os.path.isdir(session_io.session_dir(_DISPATCH_DIR, sid)):
        yield {"event": "error",
               "message": f"session {sid} does not exist"}
        return

    session_io.update_meta(_DISPATCH_DIR, sid, status="closing")

    yield _progress(0, "starting", "running", detail="Starting memory updates")

    try:
        config = _load_config()

        # Step 1: update personal memory — experiences
        yield _progress(1, "update_experiences", "running")
        status, detail = _step_update_personal_memory_file(sid, config, "experiences")
        yield _progress(1, "update_experiences", status, detail=detail)

        # Step 2: update personal memory — relationships
        yield _progress(2, "update_relationships", "running")
        status, detail = _step_update_personal_memory_file(sid, config, "relationships")
        yield _progress(2, "update_relationships", status, detail=detail)

        # Step 3: update personal memory — emotional_state
        yield _progress(3, "update_emotional_state", "running")
        status, detail = _step_update_personal_memory_file(sid, config, "emotional_state")
        yield _progress(3, "update_emotional_state", status, detail=detail)

        session_io.update_meta(_DISPATCH_DIR, sid, status="closed")

        yield {
            "event": "session_closed",
            "sid": sid,
            "duration_ms": int((time.time() - close_start) * 1000),
        }

    except Exception as e:
        import traceback
        traceback.print_exc()
        yield {"event": "error",
               "message": f"Close failed: {e}"}
