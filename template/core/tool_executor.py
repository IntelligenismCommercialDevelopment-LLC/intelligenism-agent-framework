"""
Tool registry with auto-discovery.
Scans the tools/ directory in this agent's folder, imports all *_tools.py files,
and registers their TOOLS dicts. Adding a tool = drop a .py file, no code changes.
"""

import importlib
import importlib.util
import os
import glob

AGENT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(AGENT_DIR, "tools")

REGISTRY = {}


def _discover_tools():
    """Scan tools/ directory and load all *_tools.py modules."""
    REGISTRY.clear()
    for filepath in sorted(glob.glob(os.path.join(TOOLS_DIR, "*_tools.py"))):
        module_name = os.path.basename(filepath)[:-3]
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        if hasattr(module, "TOOLS"):
            REGISTRY.update(module.TOOLS)


def execute(tool_name, arguments):
    """Execute a tool by name. Returns error string if tool not in registry."""
    if tool_name not in REGISTRY:
        return f"Error: tool '{tool_name}' not allowed"
    return REGISTRY[tool_name]["handler"](arguments)


def get_tools_schema():
    """Generate OpenAI-format tools JSON schema from registry."""
    return [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": tool["description"],
                "parameters": tool["parameters"]
            }
        }
        for name, tool in REGISTRY.items()
    ]


# Auto-discover on import
_discover_tools()
