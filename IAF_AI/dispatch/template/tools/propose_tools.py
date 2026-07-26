"""emit_propose 工具：agent 通过它提交提案竞标出价。"""
import json


def _emit_propose_handler(args):
    return json.dumps({"status": "accepted", "tool": "emit_propose", "input": args},
                      ensure_ascii=False)


TOOLS = {
    "emit_propose": {
        "description": "提交提案竞标出价（itoken ask_price）。",
        "parameters": {
            "type": "object",
            "properties": {
                "ask_price": {"type": "number", "description": "期望的 itoken 报酬"},
                "reason":    {"type": "string", "description": "出价理由（可选）"}
            },
            "required": ["ask_price"]
        },
        "handler": _emit_propose_handler
    }
}
