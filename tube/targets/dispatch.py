"""
Dispatch target — builds subprocess command to run a dispatch strategy.

Step example in tubes.json:
    {"type": "dispatch", "id": "roundtable",
     "payload": {"message": "头脑风暴讨论", "max_rounds": 5}}
"""

import sys
import os
import json


def build_command(step, project_root):
    """
    Build subprocess command list for a dispatch step.

    step:         dict from tubes.json steps array
    project_root: absolute path to framework root

    Returns: list[str] — command for subprocess.run()
    """
    tube_dir = os.path.join(project_root, "tube")
    strategy = step["id"]
    payload = step.get("payload", {})
    message = payload.get("message", payload.get("topic", ""))

    cmd = [
        sys.executable,
        os.path.join(tube_dir, "run_dispatch.py"),
        "--strategy", strategy,
        "--message", message,
    ]

    extra = {k: v for k, v in payload.items()
             if k not in ("message", "topic")}
    if extra:
        cmd.extend(["--extra", json.dumps(extra, ensure_ascii=False)])

    return cmd
