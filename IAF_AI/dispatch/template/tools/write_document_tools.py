"""
write_document_tools.py —— 【示例】固定目标 + 原子写 领域工具。

这是最小模板出厂的**唯一示例工具**，给 iaf_coder 在 Phase B「做加法」时照抄的范式。
它演示了 IAF 领域工具的三条硬规矩（见 super_chat 的 iaf_coder 施工纪律）：

  1. **只收 `content`，不收 path / 目录 / 文件名** —— 落点由代码写死，LLM 无法逃逸。
  2. **固定目标 = <shell>/workspace/<agent_id>/document.md** —— agent_id 从**本 dispatch 自己的
     dispatch_config.json 顶层字段**读（运行时分配器会把它覆盖成真实身份；所以**不能**用目录名，
     要读 config 的活值）。
  3. **原子写** —— 同目录临时文件 + os.replace；UTF-8；覆盖旧文件而非累加。

围栏：拒空内容；拒不安全 agent_id；解析后的目标必须落在本 IAF 的 workspace/ 之内。

【iaf_coder：Phase B 请把本文件改造成你的领域工具】
  - 改工具名（如 write_experiences / write_digest）、文件名（write_<domain>_tools.py）、
    落点文件名（<domain>.md）、description。
  - 若一份 IAF 要写多个产物，再加相应的 write_* handler，各自固定目标。
  - 不确定不要 free-path；固定目标是本框架的安全底线。
"""

from __future__ import annotations

import json
import os
import re
import tempfile

_TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
_DISPATCH_DIR = os.path.dirname(_TOOLS_DIR)                       # IAF_AI/dispatch/<slot>
_SHELL_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_DISPATCH_DIR)))  # shell 根
_WORKSPACE_ROOT = os.path.join(_SHELL_ROOT, "workspace")

_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")

# 【改造点】领域产物落点文件名
_OUTPUT_FILENAME = "document.md"


def _resolve_agent_id() -> str:
    """从本 dispatch 的 dispatch_config.json 顶层读 agent_id（分配器注入的活值）。"""
    cfg_path = os.path.join(_DISPATCH_DIR, "dispatch_config.json")
    with open(cfg_path, encoding="utf-8") as f:
        agent_id = json.load(f).get("agent_id", "")
    if not _AGENT_ID_RE.match(agent_id or ""):
        raise ValueError(f"不安全或缺失的 agent_id：{agent_id!r}")
    return agent_id


def _write_document(args) -> str:
    content = (args or {}).get("content", "")
    if not isinstance(content, str) or not content.strip():
        return "Error: content 为空——必须提供完整正文。"

    agent_id = _resolve_agent_id()
    out_dir = os.path.join(_WORKSPACE_ROOT, agent_id)

    # 围栏：解析后的目标必须在本 IAF workspace 之内
    real_dir = os.path.realpath(out_dir)
    real_ws = os.path.realpath(_WORKSPACE_ROOT)
    if not (real_dir == real_ws or real_dir.startswith(real_ws + os.sep)):
        return f"Error: 目标越界（须在 workspace 内）：{real_dir}"

    os.makedirs(real_dir, exist_ok=True)
    target = os.path.join(real_dir, _OUTPUT_FILENAME)

    # 原子写：同目录临时文件 + replace
    fd, tmp = tempfile.mkstemp(dir=real_dir, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
            if not content.endswith("\n"):
                f.write("\n")
        os.replace(tmp, target)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise

    return f"Written {len(content)} chars → workspace/{agent_id}/{_OUTPUT_FILENAME}"


TOOLS = {
    "write_document": {
        "description": (
            "将本轮生成的完整 markdown 正文覆盖写入本槽绑定身份的固定落点。"
            "只接受 content 一个参数；没有 path。必须调用一次以完成本轮产出。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "完整 markdown 正文（不要代码围栏，不要元对话）",
                }
            },
            "required": ["content"],
        },
        "handler": _write_document,
    },
}
