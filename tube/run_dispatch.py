#!/usr/bin/env python3
"""
CLI entry point for running a dispatch strategy via subprocess.

Used by tube_runner to invoke dispatch in isolated processes.
Also usable directly from the terminal:

    python3 tube/run_dispatch.py --strategy roundtable --message "讨论主题"
"""

import sys
import os
import json
import argparse
import importlib.util

TUBE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TUBE_DIR)


def _load_dispatch_module(strategy_name):
    """Dynamically load a dispatch strategy's module."""
    strategy_dir = os.path.join(PROJECT_ROOT, "dispatch", strategy_name)
    dispatch_path = os.path.join(strategy_dir, "dispatch.py")

    if not os.path.exists(dispatch_path):
        print(f"[run_dispatch] Strategy '{strategy_name}' not found: "
              f"{dispatch_path}", file=sys.stderr)
        sys.exit(1)

    # Strategy directory must be on sys.path for local imports
    # (dispatch_base, session_manager, context_injector)
    if strategy_dir not in sys.path:
        sys.path.insert(0, strategy_dir)
    # Project root must be on sys.path for lib/ imports
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    spec = importlib.util.spec_from_file_location(
        f"dispatch_{strategy_name}", dispatch_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description="Run a dispatch strategy")
    parser.add_argument("--strategy", required=True,
                        help="Dispatch folder name under dispatch/")
    parser.add_argument("--message", required=True,
                        help="User message / topic for the dispatch")
    parser.add_argument("--session-id", default=None,
                        help="Existing session ID (creates new if omitted)")
    parser.add_argument("--extra", default=None,
                        help="Extra payload as JSON string")
    args = parser.parse_args()

    module = _load_dispatch_module(args.strategy)

    # Build keyword arguments
    kwargs = {
        "user_message": args.message,
        "project_root": PROJECT_ROOT,
    }
    if args.session_id:
        kwargs["session_id"] = args.session_id

    # Parse extra payload for overrides (e.g. max_rounds)
    if args.extra:
        try:
            extra = json.loads(args.extra)
            if "max_rounds" in extra:
                kwargs["max_rounds_override"] = int(extra["max_rounds"])
        except (json.JSONDecodeError, ValueError):
            pass

    try:
        session_id, responses = module.run(**kwargs)
        # Output summary as JSON (parseable by tube_runner or human)
        output = {
            "session_id": session_id,
            "response_count": len(responses),
            "strategy": args.strategy,
        }
        print(json.dumps(output, ensure_ascii=False))
    except Exception as e:
        print(f"[run_dispatch] Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
