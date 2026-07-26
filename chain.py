"""
chain.py — IAF Shell 运行入口。

流程：
1. 同步 config_base → engine 内部（sync_config）
2. 实例化 TemplateDispatch 并执行
3. 产出写入 workspace/{dispatch_name}/
"""

import os
import sys
import json
import time

SHELL_ROOT = os.path.dirname(os.path.abspath(__file__))

# 确保 shell root 在 path 中，以便导入同级 sync 模块
if SHELL_ROOT not in sys.path:
    sys.path.insert(0, SHELL_ROOT)

import sync_config


def run(dispatch_name: str = "template",
        user_message: str = "",
        agent_name: str = "script_writer",
        session_id: str | None = None):
    """
    执行单次 IAF dispatch 调用。

    Args:
        dispatch_name: dispatch 策略名（对应 IAF_AI/dispatch/{name}/）
        user_message: 用户输入消息
        agent_name: 要调用的 agent 名称
        session_id: 可选 session ID，None 则自动创建
    Returns:
        dict: {text_response, tool_calls, duration_ms}
    """
    # ─── Step 1: 同步配置 ───
    print("=" * 60)
    print("[chain] Step 1: 同步 config_base → engine")
    print("=" * 60)
    sync_config.sync()

    # ─── Step 2: 实例化 dispatch ───
    agent_dir = os.path.join(SHELL_ROOT, "IAF_AI", "dispatch", dispatch_name)
    if not os.path.isdir(agent_dir):
        raise FileNotFoundError(f"Dispatch 目录不存在: {agent_dir}")

    # 确保 dispatch 目录在 path 中
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)

    # 导入 TemplateDispatch
    dispatch_module_path = os.path.join(agent_dir, "dispatch.py")
    if not os.path.isfile(dispatch_module_path):
        raise FileNotFoundError(f"dispatch.py 不存在: {dispatch_module_path}")

    import importlib.util
    spec = importlib.util.spec_from_file_location("dispatch", dispatch_module_path)
    dispatch_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(dispatch_mod)

    TemplateDispatch = dispatch_mod.TemplateDispatch

    # ─── Step 3: 执行 ───
    print("=" * 60)
    print(f"[chain] Step 2: 执行 dispatch/{dispatch_name} agent={agent_name}")
    print("=" * 60)

    disp = TemplateDispatch(agent_dir)
    disp.new_session(session_id=session_id, agent_name=agent_name)

    if user_message:
        disp.append_to_history("user", user_message)

    result = disp.run(agent_name=agent_name)

    # ─── Step 4: 写入 workspace ───
    workspace_dir = os.path.join(SHELL_ROOT, "workspace", dispatch_name)
    os.makedirs(workspace_dir, exist_ok=True)

    text_response = result.get("text_response", "")
    if text_response:
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        output_file = os.path.join(workspace_dir, f"output_{timestamp}.md")
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(text_response)
        print(f"[chain] 产出已写入: {output_file}")

    # 输出工具调用结果
    tool_calls = result.get("tool_calls", [])
    if tool_calls:
        tool_output_file = os.path.join(workspace_dir, f"tool_calls_{timestamp}.json")
        with open(tool_output_file, "w", encoding="utf-8") as f:
            json.dump(tool_calls, f, ensure_ascii=False, indent=2)
        print(f"[chain] 工具调用已写入: {tool_output_file}")

    print("=" * 60)
    print(f"[chain] 完成。耗时: {result.get('duration_ms', 0)}ms")
    print("=" * 60)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IAF Shell 运行入口")
    parser.add_argument("--dispatch", default="template", help="dispatch 策略名")
    parser.add_argument("--agent", default="script_writer", help="agent 名称")
    parser.add_argument("--message", default="", help="用户输入消息")
    parser.add_argument("--session", default=None, help="session ID（可选）")

    args = parser.parse_args()

    run(
        dispatch_name=args.dispatch,
        user_message=args.message,
        agent_name=args.agent,
        session_id=args.session,
    )
