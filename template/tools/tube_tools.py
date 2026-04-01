"""
Tube tools: trigger tubes, check status, read logs.
Gives agents the ability to drive the signal topology layer.
"""

import requests
import os

TUBE_API = os.environ.get("IAF_API_URL", "http://127.0.0.1:5000")


def _trigger_tube(args):
    """Trigger a tube by ID."""
    tube_id = args["tube_id"]
    try:
        resp = requests.post(
            f"{TUBE_API}/api/tube/trigger",
            json={"tube_id": tube_id},
            timeout=5)
        data = resp.json()
        if resp.status_code == 200:
            return f"Tube '{tube_id}' triggered successfully."
        else:
            return f"Error: {data.get('error', 'unknown')}"
    except Exception as e:
        return f"Error triggering tube: {e}"


def _list_tubes(args):
    """List all tubes with their current status."""
    try:
        resp = requests.get(f"{TUBE_API}/api/tube/status", timeout=5)
        tubes = resp.json().get("tubes", [])
        if not tubes:
            return "No tubes defined."
        lines = []
        for t in tubes:
            flag = "ON" if t["enabled"] else "OFF"
            lines.append(f"  {t['id']}  [{flag}]  {t['status']}")
        return "Tubes:\n" + "\n".join(lines)
    except Exception as e:
        return f"Error listing tubes: {e}"


def _tube_log(args):
    """Read recent tube execution log entries."""
    tube_id = args.get("tube_id", "")
    tail = args.get("tail", 20)
    try:
        params = {"tail": tail}
        if tube_id:
            params["tube_id"] = tube_id
        resp = requests.get(
            f"{TUBE_API}/api/tube/log",
            params=params, timeout=5)
        entries = resp.json().get("entries", [])
        if not entries:
            return "No log entries found."
        lines = []
        for e in entries:
            ts = e.get("timestamp", "")[:19]
            event = e.get("event", "")
            tid = e.get("tube_id", "")
            line = f"  [{ts}] {event}"
            if tid:
                line += f"  tube={tid}"
            if "exit_code" in e:
                line += f"  exit={e['exit_code']}"
            if "duration_sec" in e:
                line += f"  {e['duration_sec']}s"
            if "error" in e:
                line += f"  error: {e['error']}"
            lines.append(line)
        return "Tube log:\n" + "\n".join(lines)
    except Exception as e:
        return f"Error reading log: {e}"


TOOLS = {
    "trigger_tube": {
        "description": "触发一条 tube 信号通路，驱动 agent、dispatch 或其他 tube 执行",
        "parameters": {
            "type": "object",
            "properties": {
                "tube_id": {"type": "string", "description": "要触发的 tube ID"}
            },
            "required": ["tube_id"]
        },
        "handler": _trigger_tube
    },
    "list_tubes": {
        "description": "列出所有 tube 及其当前状态（running/idle/enabled/disabled）",
        "parameters": {
            "type": "object",
            "properties": {}
        },
        "handler": _list_tubes
    },
    "tube_log": {
        "description": "查看 tube 执行日志，可按 tube_id 过滤",
        "parameters": {
            "type": "object",
            "properties": {
                "tube_id": {"type": "string", "description": "按 tube ID 过滤（可选）"},
                "tail": {"type": "integer", "description": "返回最近几条记录，默认20"}
            }
        },
        "handler": _tube_log
    },
}
