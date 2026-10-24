"""
sync_config.py — 将 config_base 的配置同步到 IAF_AI 内部。

职责：
1. config_base/config.json → IAF_AI/config.json（直接复制）
2. config_base/{dispatch}/dispatch_config.json → IAF_AI/dispatch/{dispatch}/dispatch_config.json
   - 翻译 context_files 中的相对路径（shell-root-relative → agent_dir-relative）
   - 绝对路径和带前缀路径（wiki:, session:, briefs:）保持不变
   - ★只翻译 context_files。free_search / coding 块里的路径不翻译（要么写绝对路径，要么自己从
     agent_dir 出发算相对路径）；读不到是【静默空串】，不会报错。
3. prompt_base/{dispatch}/rules/ → IAF_AI/dispatch/{dispatch}/rules/（规则文件下发）
   - 跨轮压缩（compaction）要用的 compact_v4.md 等，权威源在 prompt_base，引擎却从自己的
     dispatch 目录解析相对路径 → 不下发则永远找不到，且是静默出错。详见 _sync_rules 的注释。
"""

import json
import os
import shutil

SHELL_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_BASE = os.path.join(SHELL_ROOT, "config_base")
IAF_AI_ROOT = os.path.join(SHELL_ROOT, "IAF_AI")
IAF_AI_DISPATCH = os.path.join(IAF_AI_ROOT, "dispatch")
SHARED_TOOLS = os.path.join(SHELL_ROOT, "tools", "_shared")   # 分角色 dispatch 共用的围栏工具正本
PROMPT_BASE = os.path.join(SHELL_ROOT, "prompt_base")         # 集群下发的 prompt 权威源（含各 dispatch 的 rules/）

# agent_dir 相对于 shell_root 的深度：IAF_AI/dispatch/{name}/ = 3 层
AGENT_DIR_TO_SHELL_ROOT = os.path.join("..", "..", "..")


def _translate_path(path: str) -> str:
    """
    将 shell-root-relative 路径翻译为 agent_dir-relative 路径。

    规则：
    - 绝对路径（/ 开头）→ 不翻译
    - 带前缀（wiki:, session:, briefs:）→ 不翻译
    - 相对路径 → 前置 ../../../ 使其从 agent_dir 出发能找到 shell root
    """
    if os.path.isabs(path):
        return path
    if ":" in path and not path.startswith("./"):
        return path
    return os.path.join(AGENT_DIR_TO_SHELL_ROOT, path)


def sync():
    """同步 config_base → IAF_AI 内部。"""
    if not os.path.isdir(CONFIG_BASE):
        print("[sync_config] config_base/ 不存在，跳过。")
        return

    # --- 1. 全局 config.json ---
    global_config_src = os.path.join(CONFIG_BASE, "config.json")
    global_config_dst = os.path.join(IAF_AI_ROOT, "config.json")

    if os.path.isfile(global_config_src):
        shutil.copy2(global_config_src, global_config_dst)
        print(f"[sync_config] config.json → {global_config_dst}")

    # --- 2. 各 dispatch 的 dispatch_config.json ---
    for dispatch_name in os.listdir(CONFIG_BASE):
        if dispatch_name.startswith("_"):        # _retired/ 等归档目录不同步
            continue
        config_dir = os.path.join(CONFIG_BASE, dispatch_name)
        if not os.path.isdir(config_dir):
            continue

        engine_dispatch_dir = os.path.join(IAF_AI_DISPATCH, dispatch_name)
        if not os.path.isdir(engine_dispatch_dir):
            print(f"[sync_config] 警告: engine dispatch '{dispatch_name}' 不存在，跳过。")
            continue

        # 规则文件先下发（与 dispatch_config.json 无关，哪怕该 dispatch 没有 config 也要下发）
        _sync_rules(dispatch_name, engine_dispatch_dir)

        src_path = os.path.join(config_dir, "dispatch_config.json")
        dst_path = os.path.join(engine_dispatch_dir, "dispatch_config.json")

        if not os.path.isfile(src_path):
            continue

        with open(src_path, "r", encoding="utf-8") as f:
            config = json.load(f)

        # 翻译 context_files 路径
        for agent_name, agent_conf in config.get("agents", {}).items():
            if "context_files" in agent_conf:
                agent_conf["context_files"] = [
                    _translate_path(p) for p in agent_conf["context_files"]
                ]

        with open(dst_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
            f.write("\n")

        print(f"[sync_config] {dispatch_name}/dispatch_config.json → {dst_path}")

        # 共享工具下发。explore_tools.py 只有 free_search 型 dispatch 用得上；
        # status_log.py 是任何带工具的 dispatch 都要的（缺失=静默降级，没有实时 STATUS 轨迹也不报错），
        # 所以 coding 型 dispatch（不配 free_search）也必须进来，只是不铺用不上的 explore_tools.py。
        agent_confs = list(config.get("agents", {}).values())
        uses_free_search = any(a.get("free_search") for a in agent_confs)
        uses_coding = any(a.get("coding") for a in agent_confs)
        if uses_free_search or uses_coding:
            _sync_tools(engine_dispatch_dir, with_explore=uses_free_search)

    print("[sync_config] 完成。")


def _sync_rules(dispatch_name: str, engine_dispatch_dir: str):
    """把 prompt_base/{dispatch}/rules/ 下的规则文件拷进引擎 dispatch 目录的 rules/。

    为什么必须下发（★这条断链曾让跨轮压缩无法使用）：
      compaction.py 读 v4_rules.compact（缺省 "rules/compact_v4.md"），
      v4_processor._resolve_rules_path 把这个相对路径 join 到【引擎 dispatch 目录】上。
      而规则文件的权威源在 prompt_base/{dispatch}/rules/ —— 不下发则引擎永远找不到。
      更糟的是 _load_rules 找不到时【不 raise】：它返回字符串
      "(V4 rules file not found: ...)"，compaction 拿这句话当 system prompt 继续调 LLM，
      把垃圾摘要写进 compacted_history.md 并给那几轮打上 trimmed_marker，
      之后加载历史时永久跳过 → 静默毁历史。
    ★ 不要改用绝对路径绕过：_resolve_rules_path 认绝对路径，但那违反集群副本安全铁律
      （配置不许依赖集群根的名字或位置）。
    没有 rules/ 目录的 dispatch 直接跳过，对它零影响。
    """
    src = os.path.join(PROMPT_BASE, dispatch_name, "rules")
    if not os.path.isdir(src):
        return
    dst = os.path.join(engine_dispatch_dir, "rules")
    os.makedirs(dst, exist_ok=True)
    for fn in sorted(os.listdir(src)):
        s = os.path.join(src, fn)
        if not os.path.isfile(s):
            continue
        shutil.copy2(s, os.path.join(dst, fn))
        print(f"[sync_config]   rules/{fn} → {dst}")


def _sync_tools(engine_dispatch_dir: str, with_explore: bool = True):
    """把共享围栏工具拷进某 dispatch 引擎目录的 tools/，并清掉遗留 marker/旧工具。

    explore_tools.py —— 分角色围栏读写，同一份代码、行为由各自 dispatch_config.json 的
      free_search 块驱动。只有 free_search 型 dispatch 用得上（引擎按路径显式 import 它），
      coding 型 dispatch 铺过去是死文件，故由 with_explore 控制。
    status_log.py   —— 逐事件实时 STATUS 轨迹，缺失时【静默降级】。任何带工具的 dispatch 都铺。
    只删 marker_*_tools.py（隔离验证期的探针），dispatch 自带的工具（如 coding_tools.py）不动。
    """
    tools_dir = os.path.join(engine_dispatch_dir, "tools")
    os.makedirs(tools_dir, exist_ok=True)
    # 清掉隔离验证期的 marker 探针（marker_*_tools.py），避免污染
    for stale in os.listdir(tools_dir):
        if stale.startswith("marker_") and stale.endswith("_tools.py"):
            os.remove(os.path.join(tools_dir, stale))
    if with_explore:
        src = os.path.join(SHARED_TOOLS, "explore_tools.py")
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(tools_dir, "explore_tools.py"))
            print(f"[sync_config]   tools/explore_tools.py → {tools_dir}")
        else:
            print(f"[sync_config] 警告: 找不到共享工具 {src}，跳过 explore_tools 同步。")
    slog = os.path.join(SHARED_TOOLS, "status_log.py")
    if os.path.isfile(slog):
        shutil.copy2(slog, os.path.join(tools_dir, "status_log.py"))
        print(f"[sync_config]   tools/status_log.py → {tools_dir}")


if __name__ == "__main__":
    sync()
