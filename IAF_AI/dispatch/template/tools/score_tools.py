"""emit_score 工具：agent 通过它结构化输出评分+押注。"""
import json


def _emit_score_handler(args):
    # dispatch 的 external-tool handler 会 ack；此处本地 handler 直接回传规整 json。
    # 实际 score json 由 chain 从 tool_calls 中提取（见 chain_score.py extract_score）。
    return json.dumps({"status": "accepted", "tool": "emit_score", "input": args},
                      ensure_ascii=False)


TOOLS = {
    "emit_score": {
        "description": "提交对被评方案的评分与 ctoken 押注。必须调用此工具输出结果。",
        "parameters": {
            "type": "object",
            "properties": {
                "score": {"type": "integer", "description": "0-100 整数评分"},
                "bet":   {"type": "integer", "description": "押多少 ctoken 赌该方案胜出"},
                "reason":{"type": "string",  "description": "一句话理由（可选）"}
            },
            "required": ["score", "bet"]
        },
        "handler": _emit_score_handler
    }
}
