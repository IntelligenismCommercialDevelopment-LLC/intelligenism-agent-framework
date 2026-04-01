"""
Verification test: proves the template architecture works.
Run from framework root: python3 tests/test_template.py
"""
import sys
import os
import shutil
import json

FRAMEWORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, FRAMEWORK)

AGENTS_DIR = os.path.join(FRAMEWORK, "agents")
TEMPLATE_DIR = os.path.join(FRAMEWORK, "template")

# ===========================================
# Test 1: Create agent from template
# ===========================================
print("=== Test 1: Create agent from template ===")
test_agent_dir = os.path.join(AGENTS_DIR, "_test_agent")
if os.path.exists(test_agent_dir):
    shutil.rmtree(test_agent_dir)
shutil.copytree(TEMPLATE_DIR, test_agent_dir)

# Customize config
config_path = os.path.join(test_agent_dir, "agent_config.json")
with open(config_path, "r") as f:
    cfg = json.load(f)
cfg["display_name"] = "Test Agent"
with open(config_path, "w") as f:
    json.dump(cfg, f, indent=2)

assert os.path.exists(os.path.join(test_agent_dir, "core", "direct_llm.py"))
assert os.path.exists(os.path.join(test_agent_dir, "tools", "file_tools.py"))
assert os.path.exists(os.path.join(test_agent_dir, "context", "sliding_window.py"))
assert os.path.exists(os.path.join(test_agent_dir, "SOUL.md"))
print("  Copied template to agents/_test_agent/")
print("  All expected files present")
print("\u2713 Agent created from template\n")

# ===========================================
# Test 2: Tool auto-discovery
# ===========================================
print("=== Test 2: Tool auto-discovery ===")
sys.path.insert(0, test_agent_dir)
from core.tool_executor import REGISTRY, execute, get_tools_schema

tools_found = list(REGISTRY.keys())
print(f"  Discovered tools: {tools_found}")
assert "read_file" in REGISTRY, "read_file not found"
assert "write_file" in REGISTRY, "write_file not found"
assert "list_dir" in REGISTRY, "list_dir not found"

# Whitelist test
result = execute("fake_tool", {})
assert "not allowed" in result, "Whitelist failed"
print("  Whitelist correctly blocked unknown tool")
print("\u2713 Tool auto-discovery works\n")

# ===========================================
# Test 3: Schema generation
# ===========================================
print("=== Test 3: Schema generation ===")
schema = get_tools_schema()
assert len(schema) == 3, f"Expected 3 tools, got {len(schema)}"
for tool in schema:
    assert tool["type"] == "function"
    assert "name" in tool["function"]
    assert "description" in tool["function"]
    assert "parameters" in tool["function"]
print(f"  Schema has {len(schema)} tools, all valid")
print("\u2713 Schema generation works\n")

# ===========================================
# Test 4: Tool execution
# ===========================================
print("=== Test 4: Tool execution ===")
test_file = os.path.join(test_agent_dir, "test_output.txt")
result = execute("write_file", {"path": test_file, "content": "hello from test"})
assert "Written" in result
result = execute("read_file", {"path": test_file})
assert "hello from test" in result
os.remove(test_file)
print("  write_file + read_file round-trip OK")
print("\u2713 Tool execution works\n")

# ===========================================
# Test 5: Context trim strategy discovery
# ===========================================
print("=== Test 5: Context strategy discovery ===")
# Import the agent engine to test trim discovery
from core.direct_llm import _TRIM_STRATEGIES, _get_trim_func
print(f"  Discovered strategies: {list(_TRIM_STRATEGIES.keys())}")
assert "sliding_window" in _TRIM_STRATEGIES, "sliding_window not found"

trim_func = _get_trim_func("sliding_window")
test_messages = [
    {"role": "system", "content": "system"},
    {"role": "user", "content": "old msg"},
    {"role": "assistant", "content": "old reply"},
    {"role": "user", "content": "current"},
]
trimmed = trim_func(test_messages, 9999)
assert trimmed[0]["content"] == "system", "System prompt should be first"
assert trimmed[-1]["content"] == "current", "Current input should be last"
print("  Trim preserves system + current input")
print("\u2713 Context strategy discovery works\n")

# ===========================================
# Test 6: SOUL.md loading
# ===========================================
print("=== Test 6: SOUL.md loading ===")
from core.direct_llm import load_system_prompt
prompt = load_system_prompt()
assert "helpful assistant" in prompt.lower() or "Agent Identity" in prompt
print(f"  SOUL.md loaded: {len(prompt)} chars")
print("\u2713 SOUL.md loading works\n")

# ===========================================
# Test 7: Agent config loading
# ===========================================
print("=== Test 7: Agent config loading ===")
from core.direct_llm import _load_config

# Check global config exists
global_config_path = os.path.join(FRAMEWORK, "config.json")
assert os.path.exists(global_config_path), "config.json not found at framework root"

with open(global_config_path, "r") as f:
    gc = json.load(f)

has_real_key = gc["providers"]["openrouter"]["key"] != "YOUR_OPENROUTER_KEY_HERE"
if has_real_key:
    config = _load_config()
    assert config["url"], "URL should be set"
    assert config["model"], "Model should be set"
    assert config["display_name"] == "Test Agent"
    print(f"  Config loaded: {config['display_name']} on {config['model']}")
    print("\u2713 Config loading works\n")
else:
    print("  Skipped (API key not set in config.json)")
    print("\u26A0 Set your key in config.json to test config loading\n")

# ===========================================
# Cleanup
# ===========================================
shutil.rmtree(test_agent_dir)
if test_agent_dir in sys.path:
    sys.path.remove(test_agent_dir)

print("=" * 50)
print("\u2713 All template architecture tests passed!")
print("=" * 50)
print()
print("Next: set your API key in config.json, then run:")
print("  python3 chat_server.py")
print("  Open http://127.0.0.1:5000")
