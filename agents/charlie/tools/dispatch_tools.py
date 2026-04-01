"""
Dispatch tools: trigger multi-agent collaboration from within an agent.
Gives agents the ability to initiate dispatch sessions and send topics for discussion.
"""

import os
import json
import requests

DISPATCH_API = os.environ.get("IAF_API_URL", "http://127.0.0.1:5000")


def _run_dispatch(args):
    """Create a new dispatch session and run a discussion on the given topic."""
    strategy = args["strategy"]
    message = args["message"]
    max_rounds = args.get("max_rounds", None)

    try:
        # Step 1: Create session
        r1 = requests.post(
            f"{DISPATCH_API}/api/dispatch/{strategy}/sessions",
            timeout=10
        )
        if r1.status_code != 200:
            return f"Error creating session: {r1.json().get('error', 'unknown')}"

        session_id = r1.json().get("session_id")

        # Step 2: Send message (non-streaming, waits for all rounds)
        payload = {"message": message}
        if max_rounds is not None:
            payload["max_rounds"] = int(max_rounds)

        r2 = requests.post(
            f"{DISPATCH_API}/api/dispatch/{strategy}/sessions/{session_id}/chat",
            json=payload,
            timeout=300  # dispatch can take several minutes
        )
        if r2.status_code != 200:
            return f"Error running dispatch: {r2.json().get('error', 'unknown')}"

        data = r2.json()
        responses = data.get("responses", [])

        # Format results for the agent to consume
        lines = [f"Dispatch '{strategy}' completed. Session: {session_id}",
                 f"Responses: {len(responses)}",
                 ""]
        for resp in responses:
            name = resp.get("display_name", resp.get("agent_id", "?"))
            rd = resp.get("round", "?")
            content = resp.get("content", "")
            # Truncate long responses
            if len(content) > 800:
                content = content[:800] + "..."
            lines.append(f"[Round {rd}] {name}:")
            lines.append(content)
            lines.append("")

        return "\n".join(lines)

    except requests.Timeout:
        return f"Error: dispatch '{strategy}' timed out"
    except Exception as e:
        return f"Error: {e}"


def _list_strategies(args):
    """List all available dispatch strategies."""
    try:
        r = requests.get(f"{DISPATCH_API}/api/dispatch", timeout=5)
        strategies = r.json().get("strategies", [])
        if not strategies:
            return "No dispatch strategies available."
        lines = ["Available dispatch strategies:"]
        for s in strategies:
            name = s.get("display_name", s.get("name", "?"))
            desc = s.get("description", "")
            has_ui = "UI" if s.get("has_ui") else ""
            lines.append(f"  {s['name']}  ({name})  {desc}  {has_ui}")
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"


TOOLS = {
    "run_dispatch": {
        "description": "发起一个多Agent协作讨论。选择一个协作策略，提供讨论主题，多个Agent将按各自角色轮流发言",
        "parameters": {
            "type": "object",
            "properties": {
                "strategy": {
                    "type": "string",
                    "description": "协作策略名称（dispatch文件夹名，如 roundtable）"
                },
                "message": {
                    "type": "string",
                    "description": "讨论的主题或问题"
                },
                "max_rounds": {
                    "type": "integer",
                    "description": "最大讨论轮数（可选，使用策略默认值）"
                }
            },
            "required": ["strategy", "message"]
        },
        "handler": _run_dispatch
    },
    "list_dispatch_strategies": {
        "description": "列出所有可用的多Agent协作策略",
        "parameters": {
            "type": "object",
            "properties": {}
        },
        "handler": _list_strategies
    },
}
