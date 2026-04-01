#!/usr/bin/env python3
"""
CLI entry point for running an agent task via subprocess.

Used by tube_runner to invoke agents in isolated processes.
Also usable directly from the terminal:

    python3 tube/run_agent.py --agent-id default --prompt "hello" --mode batch
"""

import sys
import os
import argparse
import importlib.util

TUBE_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(TUBE_DIR)


def _load_agent_engine(agent_id):
    """Dynamically load an agent's direct_llm module."""
    agent_dir = os.path.join(PROJECT_ROOT, "agents", agent_id)
    engine_path = os.path.join(agent_dir, "core", "direct_llm.py")

    if not os.path.exists(engine_path):
        print(f"[run_agent] Agent '{agent_id}' not found: {engine_path}",
              file=sys.stderr)
        sys.exit(1)

    # Agent's directory must be on sys.path for its internal imports
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)
    # Project root must be on sys.path for lib/ imports
    if PROJECT_ROOT not in sys.path:
        sys.path.insert(0, PROJECT_ROOT)

    spec = importlib.util.spec_from_file_location("agent_engine", engine_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    parser = argparse.ArgumentParser(description="Run an agent task")
    parser.add_argument("--agent-id", required=True,
                        help="Agent folder name under agents/")
    parser.add_argument("--prompt", required=True,
                        help="Message to send to the agent")
    parser.add_argument("--mode", default="batch",
                        choices=["chat", "batch"],
                        help="Run mode (default: batch)")
    args = parser.parse_args()

    engine = _load_agent_engine(args.agent_id)

    try:
        result = engine.call_agent(args.prompt, mode=args.mode)
        print(result)
    except Exception as e:
        print(f"[run_agent] Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
