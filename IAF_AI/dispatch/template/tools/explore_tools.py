"""explore_tools.py — 分角色围栏工具工厂（供引擎 free_search 模式装载）。

canonical 版住 tools/_shared/，由 sync 阶段拷进各 dispatch 的 tools/ 子目录；三角色同一份代码，
**行为由各自 dispatch_config.json 的 free_search 块驱动**（read_allow / write_allow / max_read_tokens /
can_delete）——因此同一份文件在 planner/faculty/reflector 里各自吃到不同的读写围栏。

引擎调用约定（IAF_AI/dispatch/*/dispatch.py free_search 分支）：
    make_tools(read_roots, write_root) -> {name: {description, parameters, handler}}
本工厂**忽略**引擎传入的 read_roots/write_root，改从 sibling dispatch_config.json 的 free_search 块
自解析绝对围栏（更可控、与 config 审查一致）。

🔒 安全铁律（硬编码 denylist，任何 allow 都盖不过）：
    config_base/ · IAF_AI/ · world/ · prompt_base/_golden/ · memory/experience_ledger.jsonl · *_retired/
"""

import glob as _glob
import json
import os

# ── 定位 shell_root 与本 dispatch 的 config ────────────────────────────────
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))          # …/IAF_AI/dispatch/<role>/tools
_DISPATCH_DIR = os.path.dirname(_THIS_DIR)                        # …/IAF_AI/dispatch/<role>
_SHELL_ROOT = os.path.realpath(os.path.join(_DISPATCH_DIR, "..", "..", ".."))  # synthesizer_iaf/
_CONFIG_PATH = os.path.join(_DISPATCH_DIR, "dispatch_config.json")

# 🔒 永不可碰（读可以有限放开，但写/删绝对禁）
_DENY_ALWAYS = [
    os.path.realpath(os.path.join(_SHELL_ROOT, p)) for p in [
        "config_base", "IAF_AI", "world",
        "prompt_base/_golden", "prompt_base/_retired",
        "memory/experience_ledger.jsonl", "memory/_retired",
    ]
]

READ_CAP_CHARS = 200_000       # 单文件读取上限（防单册撑爆）
GREP_CAP_HITS = 80


def _load_free_search():
    try:
        with open(_CONFIG_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception:
        return {}
    for _k, a in (cfg.get("agents") or {}).items():
        if a.get("role") == "core" and a.get("free_search"):
            return a["free_search"]
    return {}


def _abs_roots(rel_list):
    return [os.path.realpath(os.path.join(_SHELL_ROOT, p)) for p in (rel_list or [])]


def _under(path_abs, roots):
    for r in roots:
        if path_abs == r or path_abs.startswith(r + os.sep):
            return True
    return False


def _denied(path_abs):
    for d in _DENY_ALWAYS:
        if path_abs == d or path_abs.startswith(d + os.sep):
            return True
    return False


def _resolve(path, roots):
    """把用户给的（可能相对 shell_root 的）路径归一化为 abs，并校验在 roots 内、不在 denylist。
    返回 (abs_path, err)。err 非空表示越界。"""
    if os.path.isabs(path):
        p = os.path.realpath(path)
    else:
        p = os.path.realpath(os.path.join(_SHELL_ROOT, path))
    if _denied(p):
        return None, f"[拒绝] 路径命中安全 denylist：{path}"
    if not _under(p, roots):
        allowed = ", ".join(os.path.relpath(r, _SHELL_ROOT) for r in roots) or "(无)"
        return None, f"[越界] {path} 不在允许范围内。允许：{allowed}"
    return p, None


def _rel(p):
    try:
        return os.path.relpath(p, _SHELL_ROOT)
    except Exception:
        return p


def make_tools(read_roots=None, write_root=None):
    """引擎入口。忽略传入参数，从本 dispatch config 的 free_search 块自解析围栏。"""
    fs = _load_free_search()
    READ_ROOTS = _abs_roots(fs.get("read_allow") or fs.get("free_search_path") or [])
    WRITE_ROOTS = _abs_roots(fs.get("write_allow") or [])
    MAX_READ_TOKENS = int(fs.get("max_read_tokens", 120_000))
    CAN_DELETE = bool(fs.get("can_delete", False))

    budget = {"read_tokens": 0}   # 闭包内可变预算计数器（本次 run 累计）

    def _budget_note():
        used = budget["read_tokens"]
        if used >= MAX_READ_TOKENS:
            return (f"\n\n⛔ 阅读预算已用尽（已读约 {used} / {MAX_READ_TOKENS} token）。"
                    f"**立即停止探索、开始产出**：写出你的交付物，然后结束。")
        return ""

    def _over_budget():
        return budget["read_tokens"] >= MAX_READ_TOKENS

    # ── 只读探索 ───────────────────────────────────────────────────────
    def list_dir(inp):
        path = (inp or {}).get("path", ".")
        p, err = _resolve(path, READ_ROOTS)
        if err:
            return err
        if not os.path.isdir(p):
            return f"[错误] 不是目录：{_rel(p)}"
        rows = []
        for name in sorted(os.listdir(p)):
            fp = os.path.join(p, name)
            if _denied(fp):
                continue
            if os.path.isdir(fp):
                rows.append(f"  {name}/")
            else:
                try:
                    kb = os.path.getsize(fp) / 1024
                    rows.append(f"  {name}  ({kb:.1f} KB)")
                except OSError:
                    rows.append(f"  {name}")
        return f"目录 {_rel(p)}/：\n" + ("\n".join(rows) or "  (空)")

    def read_file(inp):
        if _over_budget():
            return _budget_note().strip()
        path = (inp or {}).get("path", "")
        p, err = _resolve(path, READ_ROOTS)
        if err:
            return err
        if not os.path.isfile(p):
            return f"[错误] 文件不存在：{_rel(p)}"
        try:
            with open(p, encoding="utf-8", errors="replace") as f:
                text = f.read(READ_CAP_CHARS + 1)
        except Exception as e:
            return f"[错误] 读取失败：{e}"
        truncated = len(text) > READ_CAP_CHARS
        text = text[:READ_CAP_CHARS]
        budget["read_tokens"] += len(text) // 4         # chars/4 估算
        head = f"===== {_rel(p)} ====="
        if truncated:
            head += f"\n（超过 {READ_CAP_CHARS} 字符，已截断）"
        return f"{head}\n{text}{_budget_note()}"

    def read_docx(inp):
        if _over_budget():
            return _budget_note().strip()
        path = (inp or {}).get("path", "")
        p, err = _resolve(path, READ_ROOTS)
        if err:
            return err
        if not os.path.isfile(p):
            return f"[错误] 文件不存在：{_rel(p)}"
        try:
            import docx  # python-docx
        except ImportError:
            return ("[缺依赖] 读取 .docx 需要 python-docx（pip install python-docx）。"
                    "暂时无法读取此册，请换 .md/.txt 材料或先跳过。")
        try:
            d = docx.Document(p)
            text = "\n".join(par.text for par in d.paragraphs)
        except Exception as e:
            return f"[错误] 解析 docx 失败：{e}"
        text = text[:READ_CAP_CHARS]
        budget["read_tokens"] += len(text) // 4
        return f"===== {_rel(p)} (docx) =====\n{text}{_budget_note()}"

    def grep(inp):
        inp = inp or {}
        pattern = inp.get("pattern", "")
        path = inp.get("path", ".")
        if not pattern:
            return "[错误] 需要 pattern"
        p, err = _resolve(path, READ_ROOTS)
        if err:
            return err
        import re
        try:
            rx = re.compile(pattern)
        except re.error as e:
            return f"[错误] 正则无效：{e}"
        files = []
        if os.path.isfile(p):
            files = [p]
        else:
            for fp in _glob.glob(os.path.join(p, "**", "*"), recursive=True):
                if os.path.isfile(fp) and not _denied(fp):
                    files.append(fp)
        hits = []
        for fp in files:
            try:
                with open(fp, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f, 1):
                        if rx.search(line):
                            hits.append(f"{_rel(fp)}:{i}: {line.strip()[:200]}")
                            if len(hits) >= GREP_CAP_HITS:
                                break
            except Exception:
                continue
            if len(hits) >= GREP_CAP_HITS:
                hits.append(f"…（命中已达上限 {GREP_CAP_HITS}）")
                break
        return "\n".join(hits) if hits else f"（{pattern} 在 {_rel(p)} 无命中）"

    # ── 写（受 write_allow 约束）────────────────────────────────────────
    def write_file(inp):
        inp = inp or {}
        path = inp.get("path", "")
        content = inp.get("content", "")
        p, err = _resolve(path, WRITE_ROOTS)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as f:
                f.write(content if content.endswith("\n") else content + "\n")
        except Exception as e:
            return f"[错误] 写入失败：{e}"
        return f"[已写入] {_rel(p)}（{len(content)} 字符）"

    def edit_file(inp):
        inp = inp or {}
        path = inp.get("path", "")
        old = inp.get("old_string", "")
        new = inp.get("new_string", "")
        p, err = _resolve(path, WRITE_ROOTS)
        if err:
            return err
        if not os.path.isfile(p):
            return f"[错误] 文件不存在，无法 edit：{_rel(p)}（可用 write_file 新建）"
        try:
            with open(p, encoding="utf-8") as f:
                text = f.read()
        except Exception as e:
            return f"[错误] 读取失败：{e}"
        if old and old not in text:
            return f"[错误] old_string 在 {_rel(p)} 中未找到（需逐字匹配）"
        cnt = text.count(old) if old else 0
        if old and cnt > 1:
            return f"[错误] old_string 命中 {cnt} 处，不唯一——请给更长的上下文"
        newtext = text.replace(old, new, 1) if old else new
        try:
            with open(p, "w", encoding="utf-8") as f:
                f.write(newtext)
        except Exception as e:
            return f"[错误] 写回失败：{e}"
        return f"[已编辑] {_rel(p)}"

    tools = {
        "list_dir": {
            "description": "列出允许范围内某目录的内容（子目录/文件+大小）。path 相对 shell 根，如 'library/extracted_material'。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string", "description": "目录路径（相对 shell 根）"}}},
            "handler": list_dir,
        },
        "read_file": {
            "description": "读取允许范围内一个文本文件的全文（超阅读预算后会拒读并提示产出）。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"]},
            "handler": read_file,
        },
        "read_docx": {
            "description": "读取允许范围内一个 .docx 文件的正文。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"]},
            "handler": read_docx,
        },
        "grep": {
            "description": "在允许范围内按正则检索（文件或目录递归）。返回 路径:行号: 内容。",
            "parameters": {"type": "object", "properties": {
                "pattern": {"type": "string"}, "path": {"type": "string"}},
                "required": ["pattern"]},
            "handler": grep,
        },
    }
    # 无可读根（read_allow/free_search_path 皆空）→ 不暴露任何只读探索工具，避免模型
    # 对着"永远被拒"的读工具空跑 tool loops（读侧围栏与写侧 WRITE_ROOTS 对称·治本方案二）。
    if not READ_ROOTS:
        tools.clear()
    if WRITE_ROOTS:
        tools["write_file"] = {
            "description": "在允许写入范围内新建/覆盖一个文件。path 相对 shell 根。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"]},
            "handler": write_file,
        }
        tools["edit_file"] = {
            "description": "在允许写入范围内对已存在文件做一次唯一字符串替换（old_string→new_string）。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"}},
                "required": ["path", "new_string"]},
            "handler": edit_file,
        }
    if WRITE_ROOTS and CAN_DELETE:
        def delete_file(inp):
            path = (inp or {}).get("path", "")
            p, err = _resolve(path, WRITE_ROOTS)
            if err:
                return err
            if not os.path.isfile(p):
                return f"[错误] 文件不存在：{_rel(p)}"
            try:
                os.remove(p)
            except Exception as e:
                return f"[错误] 删除失败：{e}"
            return f"[已删除] {_rel(p)}"
        tools["delete_file"] = {
            "description": "在允许写入范围内删除一个文件（谨慎）。",
            "parameters": {"type": "object", "properties": {
                "path": {"type": "string"}}, "required": ["path"]},
            "handler": delete_file,
        }

    # ── 实时 STATUS 轨迹：包裹每个 handler，进出各追加一行（best-effort，引擎零改）──
    # status_log 由 sync 拷进同目录；缺失时静默降级（工具照常工作，只是没轨迹）。
    try:
        import time as _time
        import status_log as _sl
    except Exception:
        _sl = None
    if _sl is not None:
        def _wrap(_h, _nm):
            def _inner(inp):
                _sl.tool_start(_nm, inp)
                _t0 = _time.time()
                try:
                    out = _h(inp)
                except Exception as e:
                    _sl.tool_done(_nm, _time.time() - _t0, err=e)
                    raise
                _sl.tool_done(_nm, _time.time() - _t0, out=out)
                return out
            return _inner
        for _name, _spec in tools.items():
            _spec["handler"] = _wrap(_spec["handler"], _name)

    return tools
