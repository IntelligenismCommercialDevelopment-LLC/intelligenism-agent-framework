#!/usr/bin/env python3
"""
Validation CLI for IAF AI v2.0.

Usage:
    python3 validate.py dispatch      # validate all dispatch strategies
    python3 validate.py tool          # validate all tool files in dispatch/
    python3 validate.py all           # validate everything

Output (LLM-friendly):
    OK
    FAIL: N error(s)
      - [dispatch:chat] missing dispatch_config.json
      - [tool:chat/file_tools.py] TOOLS dict not found
"""

import os
import sys
import json
import glob
import importlib.util

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
DISPATCH_DIR = os.path.join(PROJECT_ROOT, "dispatch")


def validate_dispatches():
    """Validate all dispatch strategy directories."""
    errors = []
    if not os.path.isdir(DISPATCH_DIR):
        return errors

    for name in sorted(os.listdir(DISPATCH_DIR)):
        strategy_dir = os.path.join(DISPATCH_DIR, name)
        if not os.path.isdir(strategy_dir):
            continue

        config_path = os.path.join(strategy_dir, "dispatch_config.json")
        if not os.path.isfile(config_path):
            errors.append(f"[dispatch:{name}] missing dispatch_config.json")
            continue

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                cfg = json.load(f)
        except json.JSONDecodeError as e:
            errors.append(f"[dispatch:{name}] invalid JSON in dispatch_config.json: {e}")
            continue

        if not os.path.isfile(os.path.join(strategy_dir, "dispatch.py")):
            errors.append(f"[dispatch:{name}] missing dispatch.py")

        # Validate context_files exist
        for agent_id, agent_cfg in cfg.get("agents", {}).items():
            for cf in agent_cfg.get("context_files", []):
                if cf.startswith("session:"):
                    continue  # session-relative, resolved at runtime
                full_path = os.path.join(strategy_dir, cf)
                if not os.path.isfile(full_path):
                    errors.append(f"[dispatch:{name}:{agent_id}] context_file not found: {cf}")

    return errors


def validate_tools():
    """Validate all *_tools.py files in dispatch strategy tools/ directories."""
    errors = []
    if not os.path.isdir(DISPATCH_DIR):
        return errors

    for name in sorted(os.listdir(DISPATCH_DIR)):
        strategy_dir = os.path.join(DISPATCH_DIR, name)
        tools_dir = os.path.join(strategy_dir, "tools")
        if not os.path.isdir(tools_dir):
            continue

        for filepath in sorted(glob.glob(os.path.join(tools_dir, "*_tools.py"))):
            fname = os.path.basename(filepath)
            try:
                spec = importlib.util.spec_from_file_location(
                    f"validate_{name}_{fname[:-3]}", filepath)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
            except Exception as e:
                errors.append(f"[tool:{name}/{fname}] import error: {e}")
                continue

            if not hasattr(module, "TOOLS"):
                errors.append(f"[tool:{name}/{fname}] missing TOOLS dict")
                continue

            if not isinstance(module.TOOLS, dict):
                errors.append(f"[tool:{name}/{fname}] TOOLS is not a dict")
                continue

            for tname, tdef in module.TOOLS.items():
                if "description" not in tdef:
                    errors.append(f"[tool:{name}/{fname}:{tname}] missing description")
                if "parameters" not in tdef:
                    errors.append(f"[tool:{name}/{fname}:{tname}] missing parameters")
                if "handler" not in tdef:
                    errors.append(f"[tool:{name}/{fname}:{tname}] missing handler")
                elif not callable(tdef["handler"]):
                    errors.append(f"[tool:{name}/{fname}:{tname}] handler not callable")

    return errors


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 validate.py dispatch|tool|all")
        sys.exit(1)

    target = sys.argv[1]
    if target not in ("dispatch", "tool", "all"):
        print(f"Unknown target: {target}")
        print("Usage: python3 validate.py dispatch|tool|all")
        sys.exit(1)

    errors = []

    if target in ("dispatch", "all"):
        errors.extend(validate_dispatches())
    if target in ("tool", "all"):
        errors.extend(validate_tools())

    if not errors:
        print("OK")
    else:
        print(f"FAIL: {len(errors)} error(s)")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
