"""
sync_config.py — 将 config_base 的配置同步到 IAF_AI 内部。

职责：
1. config_base/config.json → IAF_AI/config.json（直接复制）
2. config_base/{dispatch}/dispatch_config.json → IAF_AI/dispatch/{dispatch}/dispatch_config.json
   - 翻译 context_files 中的相对路径（shell-root-relative → agent_dir-relative）
   - 绝对路径和带前缀路径（wiki:, session:, briefs:）保持不变
"""

import json
import os
import shutil

SHELL_ROOT = os.path.dirname(os.path.abspath(__file__))
CONFIG_BASE = os.path.join(SHELL_ROOT, "config_base")
IAF_AI_ROOT = os.path.join(SHELL_ROOT, "IAF_AI")
IAF_AI_DISPATCH = os.path.join(IAF_AI_ROOT, "dispatch")

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
        config_dir = os.path.join(CONFIG_BASE, dispatch_name)
        if not os.path.isdir(config_dir):
            continue

        engine_dispatch_dir = os.path.join(IAF_AI_DISPATCH, dispatch_name)
        if not os.path.isdir(engine_dispatch_dir):
            print(f"[sync_config] 警告: engine dispatch '{dispatch_name}' 不存在，跳过。")
            continue

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

    print("[sync_config] 完成。")


if __name__ == "__main__":
    sync()
