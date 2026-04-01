"""
Manual trigger — fires when a flag file exists for this tube.

Flag files are created by:
  - POST /api/tube/trigger  (from UI, AI agent, or curl)
  - Direct file creation:  touch tube/manual_triggers/{tube_id}.flag

Config: not needed (empty dict is fine).
"""

import os


def check(config, state):
    """
    Return True if a flag file exists for this tube, then delete it.

    config: {}
    state:  {"tube_id": str, "flag_dir": str, ...}
    """
    tube_id = state["tube_id"]
    flag_dir = state["flag_dir"]
    flag_path = os.path.join(flag_dir, f"{tube_id}.flag")

    if os.path.exists(flag_path):
        try:
            os.remove(flag_path)
        except OSError:
            pass
        return True

    return False
