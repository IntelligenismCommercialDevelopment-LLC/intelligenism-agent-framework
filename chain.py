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
import shutil

SHELL_ROOT = os.path.dirname(os.path.abspath(__file__))
WORKSPACE   = os.path.join(SHELL_ROOT, "workspace")        # 留痕档根 {session_id}/
CURRENT_LINK = os.path.join(WORKSPACE, "current")          # ★软链 → 本次运行 workspace/{session_id}/


def _find_cluster_root(start=SHELL_ROOT):
    """从 start 上溯找含 config_base/cluster_manifest.json 的目录 = 本集群根（agent 根）。
    ★统一锚定约定（rebuild_plan0905 · I1+P1）：器官恒住 <cluster_root>/<group>/<unit>_iaf/（depth-2）。
    marker 锚定·name-free + depth-free：免疫集群改名 / 分组下沉(infra_unit_base) / 多 agent。
    要读集群级共享物（search_material 等）时用本 helper 取根，勿数 dirname；config 侧跨界围栏用 ../../。"""
    d = os.path.abspath(start)
    while True:
        if os.path.isfile(os.path.join(d, "config_base", "cluster_manifest.json")):
            return d
        p = os.path.dirname(d)
        if p == d:
            raise RuntimeError("找不到集群根（含 config_base/cluster_manifest.json 的祖先）: " + str(start))
        d = p


# 确保 shell root 在 path 中，以便导入同级 sync 模块
if SHELL_ROOT not in sys.path:
    sys.path.insert(0, SHELL_ROOT)

import sync_config

# 逐事件实时 STATUS 轨迹（与引擎 explore_tools 共用同一份 canonical 正本 tools/_shared/status_log.py）。
# 缺失时静默降级——工具照常工作，只是没轨迹。写入 <shell>/workspace/current/STATUS.md（current 软链目标 = 本次 session 档）。
sys.path.insert(0, os.path.join(SHELL_ROOT, "tools", "_shared"))
try:
    import status_log
except Exception:
    status_log = None

# 图片入料：把本机图片文件拼成 user 回合的多模态 content parts（纯函数·零网络，见该文件头部）。
# 这里是硬 import 而不是 try/except：它是本壳自带的文件，缺了就该当场报错——
# 静默降级的后果是 --image 给了图却照常跑完，产出一份「没看图的回答」，还不报错。
import image_parts


def point_current(session_dir):
    """把 workspace/current 软链指到本次运行的 {session_id} 目录（替换旧链）。
    free_search 写围栏（write_allow=workspace/current）与 STATUS 轨迹都用静态 workspace/current/*，物理落在 {session_id}/。"""
    os.makedirs(session_dir, exist_ok=True)
    # 按类型稳健删除旧 current：软链/文件用 remove，真目录用 rmtree（macOS 删目录抛 PermissionError 非 IsADirectoryError）。
    if os.path.islink(CURRENT_LINK) or os.path.isfile(CURRENT_LINK):
        os.remove(CURRENT_LINK)
    elif os.path.isdir(CURRENT_LINK):
        shutil.rmtree(CURRENT_LINK)
    # ★相对软链（current → 同级 session_id）：免疫整树迁移（如器官下沉 infra_unit_base）。绝对路径迁移后断裂。
    os.symlink(os.path.basename(os.path.normpath(session_dir)), CURRENT_LINK)


def run(dispatch_name: str = "template",
        user_message: str = "",
        agent_name: str = "core",
        session_id: str | None = None,
        image_paths: list[str] | None = None,
        image_detail: str | None = None):
    """
    执行单次 IAF dispatch 调用。

    Args:
        dispatch_name: dispatch 策略名（对应 IAF_AI/dispatch/{name}/）
        user_message: 用户输入消息
        agent_name: 要调用的 agent 名称
        session_id: 可选 session ID，None 则自动创建
        image_paths: 本机图片文件路径列表（PNG/JPEG/GIF/WebP）。给了图就要求该
            agent 的 model 是支持看图的那一档，否则服务端把图当未知入参，
            回答照常生成、看起来完全正常——这是要花钱才看得见的一类失败。
        image_detail: 取图精度旋钮，透传给服务端；不传用服务端默认
    Returns:
        dict: {text_response, tool_calls, duration_ms}
    """
    # ─── Step 1: 同步配置 ───
    print("=" * 60)
    print("[chain] Step 1: 同步 config_base → engine")
    print("=" * 60)
    sync_config.sync()

    # ─── Step 1.5: 建本次 session 档 + current 软链 + 开 STATUS 轨迹 ───
    # free_search 写围栏落 workspace/current；STATUS 逐事件轨迹也落 workspace/current/STATUS.md。
    ws_session = f"session_{time.strftime('%Y%m%d-%H%M%S')}"
    session_dir = os.path.join(WORKSPACE, ws_session)
    point_current(session_dir)                       # workspace/current → 本次 session 档
    if status_log:
        status_log.cycle_header(ws_session, f"dispatch/{dispatch_name}", 1)

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

    # 无图 ⇒ build_content 返回字符串，与不带图时的老形状逐字一致；
    # 有图 ⇒ 返回 parts 列表，一路透传到服务端（引擎不碰 content，见架构说明 §15）。
    user_content = image_parts.build_content(user_message, image_paths, detail=image_detail)
    if user_content:
        disp.append_to_history("user", user_content)

    if status_log:
        status_log.turn_header(dispatch_name, f"运行 dispatch/{dispatch_name} agent={agent_name}")
        status_log.llm_started(dispatch_name)

    result = disp.run(agent_name=agent_name)

    if status_log:
        status_log.response(dispatch_name, result.get("text_response", ""))

    # ─── Step 4: 写入 workspace ───
    workspace_dir = os.path.join(SHELL_ROOT, "workspace", dispatch_name)
    os.makedirs(workspace_dir, exist_ok=True)

    # 两份产出共用同一个时间戳，所以它必须在两个分支之外取。
    # （在 if text_response 里面取的话，这一轮只有工具调用、没有文本时，
    #   下面写 tool_calls 的分支就撞上 UnboundLocalError —— 整轮白跑且钱已付。）
    timestamp = time.strftime("%Y%m%d_%H%M%S")

    text_response = result.get("text_response", "")
    if text_response:
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

    if status_log:
        status_log.footer(f"归档于 workspace/{ws_session}/。耗时 {result.get('duration_ms', 0)}ms")

    print("=" * 60)
    print(f"[chain] 完成。耗时: {result.get('duration_ms', 0)}ms · 轨迹 workspace/current/STATUS.md")
    print("=" * 60)

    return result


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="IAF Shell 运行入口")
    parser.add_argument("--dispatch", default="template", help="dispatch 策略名")
    parser.add_argument("--agent", default="core", help="agent 名称")
    parser.add_argument("--message", default="", help="用户输入消息")
    parser.add_argument("--session", default=None, help="session ID（可选）")
    parser.add_argument("--image", action="append", default=None,
                        help="随消息一起看的图片文件（PNG/JPEG/GIF/WebP）；可重复")
    parser.add_argument("--image-detail", default=None,
                        help="取图精度，透传给服务端（如 auto）；不传用服务端默认")

    args = parser.parse_args()

    run(
        dispatch_name=args.dispatch,
        user_message=args.message,
        agent_name=args.agent,
        session_id=args.session,
        image_paths=args.image,
        image_detail=args.image_detail,
    )
