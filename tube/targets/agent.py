"""
Agent target — builds subprocess command to run an agent task.

Step example in tubes.json:
    {"type": "agent", "id": "default", "mode": "batch",
     "payload": {"prompt": "搜集AI新闻"}}
"""

import sys
import os


def build_command(step, project_root):
    """
    Build subprocess command list for an agent step.

    step:         dict from tubes.json steps array
    project_root: absolute path to framework root

    Returns: list[str] — command for subprocess.run()
    """
    tube_dir = os.path.join(project_root, "tube")
    agent_id = step["id"]
    mode = step.get("mode", "batch")
    payload = step.get("payload", {})
    prompt = payload.get("prompt", "")

    return [
        sys.executable,
        os.path.join(tube_dir, "run_agent.py"),
        "--agent-id", agent_id,
        "--mode", mode,
        "--prompt", prompt,
    ]
