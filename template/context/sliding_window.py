"""
Sliding window context trimming strategy.
Keeps system prompt (first) and current input (last) intact.
Trims oldest history messages until total fits within budget.
"""

import sys
import os

# Ensure lib/ is importable
FRAMEWORK_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if FRAMEWORK_ROOT not in sys.path:
    sys.path.insert(0, FRAMEWORK_ROOT)

from lib.token_utils import estimate_tokens


def trim(messages, budget):
    """
    Trim messages to fit within token budget.
    Fixed: first message (system) and last message (current input).
    Trimmable: everything in between (history), oldest dropped first.
    """
    if not messages:
        return messages

    fixed_head = [messages[0]]
    fixed_tail = [messages[-1]]
    middle = messages[1:-1] if len(messages) > 2 else []

    fixed_tokens = sum(estimate_tokens(m.get("content", "")) for m in fixed_head + fixed_tail)
    history_budget = budget - fixed_tokens

    if history_budget <= 0:
        return fixed_head + fixed_tail

    # Keep from newest backwards
    kept = []
    used = 0
    for msg in reversed(middle):
        cost = estimate_tokens(msg.get("content", ""))
        if used + cost > history_budget:
            break
        kept.insert(0, msg)
        used += cost

    return fixed_head + kept + fixed_tail
