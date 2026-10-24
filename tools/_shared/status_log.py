"""status_log.py — 逐事件实时 STATUS.md 轨迹（移植自 super_chat dispatch_routes 的 _status_*）。

canonical 版住 tools/_shared/，由 sync 拷进各 dispatch 的 tools/（供 explore_tools 同目录 import），
chain.py 也直接 import 本文件。两处共用同一套 append 逻辑，目标同一个文件：
    <shell_root>/workspace/current/STATUS.md
（workspace/current 是 chain 每周期指向本周期档 session_{id}/ 的软链——所以轨迹落在本周期归档里。）

★统一版（2026-09-09）：全 shell 字节一致。两处外置/新增，令本体不再按器官分叉——
  - 每器官定制的注意力 ledger 监视根，外置到本 shell 的 tools/_shared/ledger_roots.json（纯数据·缺失=ledger 关）。
  - 每条事件在写本 shell STATUS.md 的同时，附一条带 [organ] 前缀的行汇入集群根 total_status.md
    （各 IAF 内部事件的实时汇流·master 启动截空单 run·`tail -f total_status.md` 实时监控）。best-effort，绝不打断角色。

设计要点，与 super_chat 等价：
- **逐事件追加**（append-only），不是周期末一次性 dump——因此被 kill 也留得住已发生的轨迹。
- **best-effort**：任何写入失败都静默吞掉，绝不打断角色运行或工具调用。
- 事件形态复刻 super_chat：### Turn 块 → 🧠 llm_started → ▶/✓ 工具 → 💬 response。
"""

import json
import os
import time

# ── 定位 shell_root：从本文件向上走，找到含 workspace/ 的那层 ──────────────
# （本文件可能在 tools/_shared/ 或被 sync 到 IAF_AI/dispatch/<role>/tools/，深度不定，故上溯探测。）
def _find_shell_root():
    d = os.path.dirname(os.path.abspath(__file__))
    for _ in range(8):
        if os.path.isdir(os.path.join(d, "workspace")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 兜底：假定 tools/_shared/ 布局（../..）
    return os.path.realpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


_SHELL_ROOT = _find_shell_root()
_ORGAN = os.path.basename(_SHELL_ROOT).replace("_iaf", "")   # 汇流用器官标签（如 thinker）


# ── 定位集群根（total_status.md 落点）：从 shell_root 上溯找 config_base/cluster_manifest.json（P1 marker·name-free）──
def _find_cluster_root():
    d = _SHELL_ROOT
    for _ in range(6):
        if os.path.isfile(os.path.join(d, "config_base", "cluster_manifest.json")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 兜底：I1 depth-2（<cluster>/<group>/<unit>_iaf → 上溯两层）
    return os.path.dirname(os.path.dirname(_SHELL_ROOT))


_TOTAL_STATUS = os.path.join(_find_cluster_root(), "total_status.md")


def _status_path():
    # 写 workspace/current/STATUS.md（软链目标 = 本周期 session 档）
    return os.path.join(_SHELL_ROOT, "workspace", "current", "STATUS.md")


# ── 注意力 op-ledger（改造文档 Part A / M1）──────────────────────────────────
# chain 与工具 _wrap 同进程共用本模块实例，故 _STATE 天然共享：cycle_header 存 session/step，
# turn_header 存 role，tool_start 暂存 path，tool_done 成功分支 emit 一条机器可读记录。
# 自包含（不 import 任何本壳模块：本文件会被 sync 拷进各 dispatch/tools，壳根的模块不随行）。
_STATE = {"session_id": "", "step": 0, "role": "", "_pending": {}}

_LEDGER_TOOLS = {"read_file", "read_docx", "write_file", "edit_file",
                 "delete_file", "list_dir", "grep"}


def _load_ledger_roots():
    """每器官定制：注意力 ledger 监视哪些目录前缀（LLM 视角相对路径）。外置到本 shell 的
    tools/_shared/ledger_roots.json（纯数据·按器官不同）。缺失/空 → 返回 ()，ledger 关闭
    （无消费者、无产出的器官即此，如 thinker/web_forager）。"""
    try:
        with open(os.path.join(_SHELL_ROOT, "tools", "_shared", "ledger_roots.json"), encoding="utf-8") as f:
            roots = json.load(f)
        return tuple(roots) if isinstance(roots, list) else ()
    except Exception:
        return ()


_LEDGER_ROOTS = _load_ledger_roots()
_LEDGER_FAIL_PREFIXES = ("[越界]", "[错误]", "[拒绝]", "[缺依赖]")


def _ledger_path():
    return os.path.join(_SHELL_ROOT, "world", "attention_ledger.jsonl")


def _norm_path(path):
    if not path:
        return ""
    p = str(path).strip().strip("'\"").replace("\\", "/")
    if p.startswith("./"):
        p = p[2:]
    return p.rstrip("/ ").strip()


def _emit_ledger(tool, path, ok, ts):
    """append 一条 op-ledger（best-effort，任何异常静默吞掉，绝不打断工具）。
    _LEDGER_ROOTS 为空 ⟹ startswith(()) 恒 False ⟹ 直接跳过（ledger 关闭）。"""
    try:
        path = _norm_path(path)
        if tool not in _LEDGER_TOOLS or not path.startswith(_LEDGER_ROOTS):
            return
        rec = {"session_id": _STATE["session_id"], "step": _STATE["step"],
               "role": _STATE["role"], "tool": tool, "path": path,
               "ok": bool(ok), "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
        p = _ledger_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def append(line):
    """best-effort 追加一行到本 shell STATUS.md；同时附 [organ] 前缀汇入集群根 total_status.md。
    两处独立 try，任一失败静默吞掉，绝不打断角色。total_status 是各 IAF 事件的实时汇流（tail -f 监控）。"""
    try:
        p = _status_path()
        os.makedirs(os.path.dirname(p), exist_ok=True)   # current 软链存在即写进目标档
        with open(p, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    try:
        tagged = line.replace("\n", "\n[%s] " % _ORGAN)   # 逐物理行贴标签，便于 grep "[organ]" 过滤
        with open(_TOTAL_STATUS, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (_ORGAN, tagged))
    except Exception:
        pass


def _ts():
    return time.strftime("%H:%M:%S")


# ── 周期/回合级 ─────────────────────────────────────────────────────────────
def cycle_header(session_id, world_time, step):
    _STATE["session_id"] = session_id
    _STATE["step"] = step
    _STATE["_pending"] = {}
    append(f"# STATUS · {session_id}\n\n世界时间：{world_time} · 第 {step} 步\n"
           f"（逐事件实时轨迹——被中断也留得住已发生的部分。）")


def turn_header(role, detail=""):
    _STATE["role"] = role
    line = f"\n---\n### Turn [{_ts()}]  role={role}"
    if detail:
        line += f"\n  {detail}"
    append(line)


def llm_started(role, model=""):
    append(f"  - [{_ts()}] 🧠 {role} llm_started · model={model}")


def response(role, text):
    snip = str(text or "").replace("\n", " ")[:180]
    append(f"  - [{_ts()}] 💬 {role}: {snip}")


def error(msg):
    append(f"  - [{_ts()}] ✗ ERROR: {str(msg)[:180]}")


def footer(note=""):
    append(f"\n---\n归档于 {_ts()}。{note}")


# ── 工具级（explore_tools 每个 handler 进出各调一次）──────────────────────────
def tool_start(tool, inp):
    # live 无截断：拿到的是完整 inp dict，直接暂存真 path（回填才需靠完成行救 path）。
    try:
        _STATE["_pending"][tool] = (inp or {}).get("path") if isinstance(inp, dict) else None
    except Exception:
        pass
    s = str(inp if inp is not None else "")
    if len(s) > 300:
        s = s[:300] + "…"
    append(f"  - [{_ts()}] ▶ `{tool}` {s}")


def tool_done(tool, dur_s=None, out=None, err=None):
    d = f" ({dur_s:.1f}s)" if isinstance(dur_s, (int, float)) else ""
    path = _STATE["_pending"].pop(tool, None) if isinstance(_STATE.get("_pending"), dict) else None
    if err is not None:
        _emit_ledger(tool, path, ok=False, ts=_ts())
        append(f"  - [{_ts()}] ✗ `{tool}` failed{d}: {str(err)[:120]}")
        return
    meta = ""
    head = ""
    if out is not None:
        s = str(out)
        meta = f" → {len(s)}c"
        # 首行给个语义线索（如 [已写入] / [越界] / ===== 文件名）
        head = s.strip().splitlines()[0][:60] if s.strip() else ""
        if head:
            meta += f" · {head}"
    # 软失败按 head 前缀判：handler 返回 [越界]/[错误] 等字符串时不 raise、仍走这里，不计成功。
    ok = not head.startswith(_LEDGER_FAIL_PREFIXES)
    _emit_ledger(tool, path, ok=ok, ts=_ts())
    append(f"  - [{_ts()}] ✓ `{tool}` done{d}{meta}")
