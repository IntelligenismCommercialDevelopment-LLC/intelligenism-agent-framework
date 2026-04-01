"""
Cron trigger — fires when current time matches a cron expression.

Dependency: pip install croniter

Config example in tubes.json:
    {"type": "cron", "config": {"expr": "0 3 * * *"}}
"""

from datetime import timedelta

try:
    from croniter import croniter
except ImportError:
    raise ImportError(
        "croniter is required for cron triggers. "
        "Install it: pip install croniter"
    )


def check(config, state):
    """
    Return True if a cron fire time exists between last_triggered and now.

    config: {"expr": "0 3 * * *"}
    state:  {"now": datetime, "last_triggered": datetime | None, ...}
    """
    expr = config.get("expr")
    if not expr:
        return False

    now = state["now"]
    last = state.get("last_triggered")

    if last is None:
        # First run: check if we are within the current minute of a match
        cron = croniter(expr, now - timedelta(seconds=60))
        next_time = cron.get_next(type(now))
        return next_time <= now

    # Normal: is there a scheduled time between last_triggered and now?
    cron = croniter(expr, last)
    next_time = cron.get_next(type(now))
    return next_time <= now
