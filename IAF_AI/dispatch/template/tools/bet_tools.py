"""emit_bet 工具：agent 通过它提交押注竞标。"""
import json


def _emit_bet_handler(args):
    return json.dumps({"status": "accepted", "tool": "emit_bet", "input": args},
                      ensure_ascii=False)


TOOLS = {
    "emit_bet": {
        "description": "提交押注竞标（itoken principal）。",
        "parameters": {
            "type": "object",
            "properties": {
                "principal": {"type": "number", "description": "押注的 itoken 本金"},
                "reason":    {"type": "string", "description": "押注理由（可选）"}
            },
            "required": ["principal"]
        },
        "handler": _emit_bet_handler
    }
}
