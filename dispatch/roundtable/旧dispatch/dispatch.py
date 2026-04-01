"""
Roundtable Dispatch — Multi-agent roundtable discussion orchestration

After the user speaks, participating agents take turns responding for
max_rounds rounds (as defined in dispatch_config.json). Each agent can
see all previous messages in the session via staging/session_history.md.
When rounds complete, the system waits for the user's next input.

Agents can use tools (web search, file operations, etc.) during their
turn. The tool loop cycles between LLM and tool_executor until the LLM
returns a text response. Tool call history is saved as private agent
memory in staging/{agent_id}_memory.md.

Entry points:
    new_session(project_root)
    run(user_message, project_root, session_id)
    get_session_history(project_root, session_id)
    get_all_sessions(project_root)

Call chain:
    dispatch.py
    ├── Read dispatch_config.json
    ├── session_manager (create / append / load / format)
    ├── Write staging/session_history.md
    ├── context_injector.build_context() per agent
    ├── _load_agent_tools() from agents/{id}/tools/
    └── _call_with_tool_loop() → LLM ↔ tool_executor cycle
"""

import json
import os
import sys
import time
import importlib
import importlib.util
import glob


# ---------------------------------------------------------------------------
# Resolve paths and imports
# ---------------------------------------------------------------------------

_DISPATCH_DIR = os.path.dirname(os.path.abspath(__file__))

from session_manager import (
    create_session,
    append_to_session,
    load_session,
    list_sessions,
    delete_session,
    format_session_history,
)
from context_injector import build_context
from context.sliding_window import trim_records


def _get_llm_caller(project_root):
    """Import and return lib.llm_client.call_llm from the project.
    Returns None if not available (allows testing with mock)."""
    try:
        lib_path = os.path.join(project_root, "lib")
        if lib_path not in sys.path:
            sys.path.insert(0, lib_path)
        from llm_client import call_llm
        return call_llm
    except ImportError:
        return None


def _resolve_llm_endpoint(provider, global_config):
    """
    Resolve a provider name to (url, key) using global config.json.

    Args:
        provider:      Provider name, e.g. "openrouter"
        global_config: Parsed config.json dict

    Returns:
        (url, key) tuple. Falls back to default_provider if provider
        not found in config.
    """
    providers = global_config.get("providers", {})

    # Try the specified provider first
    if provider in providers:
        p = providers[provider]
        return p.get("url", ""), p.get("key", "")

    # Fall back to default_provider
    default = global_config.get("default_provider", "")
    if default and default in providers:
        p = providers[default]
        return p.get("url", ""), p.get("key", "")

    # Last resort: use first available provider
    if providers:
        first = next(iter(providers.values()))
        return first.get("url", ""), first.get("key", "")

    return "", ""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _load_config():
    config_path = os.path.join(_DISPATCH_DIR, "dispatch_config.json")
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)


def _sessions_dir():
    return os.path.join(_DISPATCH_DIR, "sessions")


def _staging_dir():
    return os.path.join(_DISPATCH_DIR, "staging")


# ---------------------------------------------------------------------------
# Tool loading
# ---------------------------------------------------------------------------

def _load_agent_tools(agent_id, project_root):
    """
    Load tools from an agent's tools/ directory.

    Scans agents/{agent_id}/tools/*_tools.py for TOOLS dicts.
    Each TOOLS dict maps tool_name -> {"function": fn, "description": ...,
    "parameters": ...}.

    Returns:
        (tool_definitions, tool_functions) where:
        - tool_definitions: list of dicts for LLM API tools parameter
        - tool_functions:   dict mapping tool_name -> callable
        Returns ([], {}) if no tools found.
    """
    tools_dir = os.path.join(project_root, "agents", agent_id, "tools")
    if not os.path.isdir(tools_dir):
        return [], {}

    tool_files = glob.glob(os.path.join(tools_dir, "*_tools.py"))
    if not tool_files:
        return [], {}

    tool_definitions = []
    tool_functions = {}

    for filepath in tool_files:
        module_name = os.path.basename(filepath)[:-3]  # strip .py
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"[dispatch] Warning: could not load {filepath}: {e}")
            continue

        tools_dict = getattr(module, "TOOLS", None)
        if not tools_dict or not isinstance(tools_dict, dict):
            continue

        for tool_name, tool_info in tools_dict.items():
            fn = tool_info.get("function")
            if fn is None:
                continue

            tool_functions[tool_name] = fn

            # Build tool definition for LLM API
            tool_definitions.append({
                "type": "function",
                "function": {
                    "name": tool_name,
                    "description": tool_info.get("description", ""),
                    "parameters": tool_info.get("parameters", {}),
                },
            })

    return tool_definitions, tool_functions


# ---------------------------------------------------------------------------
# Tool loop
# ---------------------------------------------------------------------------

def _call_with_tool_loop(
    messages, url, key, model,
    call_llm_fn, tool_definitions, tool_functions,
    max_tool_loops=10,
):
    """
    Call LLM with tool loop support.

    If the agent has no tools, this is a single LLM call returning text.
    If the agent has tools, cycles between LLM and tool execution until
    the LLM returns a text response or max_tool_loops is reached.

    Args:
        messages:          Ready-to-send messages array
        url:               LLM API endpoint URL
        key:               API key
        model:             LLM model string
        call_llm_fn:       LLM call function: call_llm(url, key, model, messages, tools=None)
        tool_definitions:  Tool defs for LLM API (can be empty)
        tool_functions:    Dict of tool_name -> callable (can be empty)
        max_tool_loops:    Safety limit on tool call cycles

    Returns:
        (content, tool_history) where:
        - content:      Final text response from LLM
        - tool_history: List of tool call records (empty if no tools used)
    """
    tool_history = []

    # --- No tools: simple single call ---
    if not tool_definitions or not tool_functions:
        response = call_llm_fn(url, key, model, messages)
        content = _extract_text(response)
        return content, tool_history

    # --- With tools: enter tool loop ---
    for loop in range(max_tool_loops):
        response = call_llm_fn(url, key, model, messages, tools=tool_definitions)

        # Parse response
        text_content, tool_calls = _parse_llm_response(response)

        # If LLM returned text (no tool calls), we're done
        if not tool_calls:
            return text_content, tool_history

        # Process each tool call
        for tool_call in tool_calls:
            tool_name = tool_call.get("name", "")
            tool_input = tool_call.get("input", {})
            tool_id = tool_call.get("id", "")

            # Execute tool
            if tool_name in tool_functions:
                try:
                    tool_result = tool_functions[tool_name](**tool_input)
                    if not isinstance(tool_result, str):
                        tool_result = json.dumps(tool_result, ensure_ascii=False)
                except Exception as e:
                    tool_result = f"Tool execution error: {e}"
            else:
                tool_result = f"Unknown tool: {tool_name}"

            # Record to tool history (for private memory)
            tool_history.append({
                "tool": tool_name,
                "input": tool_input,
                "output": tool_result[:2000],  # truncate for memory
            })

            # Append assistant's tool request + tool result to messages
            # for the next LLM call
            messages.append({
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": tool_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(tool_input, ensure_ascii=False),
                    },
                }],
            })
            messages.append({
                "role": "tool",
                "tool_call_id": tool_id,
                "content": tool_result,
            })

    # Max loops reached — return whatever text we have
    return text_content or "[Tool loop limit reached]", tool_history


def _extract_text(response):
    """Extract text content from call_llm response.
    call_llm returns: {"role": "assistant", "content": "text here"}"""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return response.get("content", "") or ""
    return str(response)


def _parse_llm_response(response):
    """
    Parse LLM response into text content and tool calls.

    call_llm returns one of:
      - {"role": "assistant", "content": "text"}          → pure text
      - {"role": "assistant", "tool_calls": [...]}         → tool calls
      - Plain string (from mock in testing)
      - List of content blocks (Anthropic-style)

    Returns:
        (text_content, tool_calls) where tool_calls is a list of
        {"id": ..., "name": ..., "input": {...}} dicts, empty if none.
    """
    # Plain string (mock or simple response)
    if isinstance(response, str):
        return response, []

    # List of content blocks (Anthropic-style)
    if isinstance(response, list):
        text_parts = []
        tool_calls = []
        for block in response:
            if block.get("type") == "text":
                text_parts.append(block.get("text", ""))
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id", ""),
                    "name": block.get("name", ""),
                    "input": block.get("input", {}),
                })
        return "\n".join(text_parts), tool_calls

    # Dict response
    if isinstance(response, dict):
        # Direct message dict from call_llm:
        # {"role": "assistant", "content": "text", "tool_calls": [...]}
        if "role" in response:
            text = response.get("content", "") or ""
            raw_tool_calls = response.get("tool_calls", [])
            if not raw_tool_calls:
                return text, []
            tool_calls = []
            for tc in raw_tool_calls:
                fn = tc.get("function", {})
                try:
                    args = json.loads(fn.get("arguments", "{}"))
                except (json.JSONDecodeError, TypeError):
                    args = {}
                tool_calls.append({
                    "id": tc.get("id", ""),
                    "name": fn.get("name", ""),
                    "input": args,
                })
            return text, tool_calls

        # {"type": "text", "content": "..."}
        if response.get("type") == "text":
            return response.get("content", ""), []

        # {"type": "tool_use", ...}
        if response.get("type") == "tool_use":
            return "", [{
                "id": response.get("id", ""),
                "name": response.get("name", ""),
                "input": response.get("input", {}),
            }]

        # Anthropic API response: {"content": [...blocks...]}
        if "content" in response and isinstance(response["content"], list):
            return _parse_llm_response(response["content"])

        # OpenAI-style wrapper: {"choices": [{"message": {...}}]}
        choices = response.get("choices", [])
        if choices:
            msg = choices[0].get("message", {})
            return _parse_llm_response(msg)

    # Fallback
    return str(response), []


# ---------------------------------------------------------------------------
# Private agent memory
# ---------------------------------------------------------------------------

def _write_agent_memory(agent_id, tool_history):
    """
    Write tool call history to staging/{agent_id}_memory.md.
    This file is only included in that agent's context_files,
    making it private to that agent.

    Creates/overwrites the file. If tool_history is empty, writes
    empty string (agent has no private memory this session).
    """
    staging = _staging_dir()
    os.makedirs(staging, exist_ok=True)
    memory_file = os.path.join(staging, f"{agent_id}_memory.md")

    if not tool_history:
        with open(memory_file, "w", encoding="utf-8") as f:
            f.write("")
        return

    parts = ["## Private Memory (Tool Results)\n"]
    for entry in tool_history:
        tool = entry.get("tool", "unknown")
        tool_input = entry.get("input", {})
        output = entry.get("output", "")

        input_str = json.dumps(tool_input, ensure_ascii=False, indent=None)
        parts.append(f"### {tool}: {input_str}")
        parts.append(f"{output}\n")

    with open(memory_file, "w", encoding="utf-8") as f:
        f.write("\n".join(parts))


# ---------------------------------------------------------------------------
# Staging file management
# ---------------------------------------------------------------------------

def _write_staging_history(project_root, session_id):
    """Load session records, trim if needed, format as markdown, write to
    staging/session_history.md. Creates staging dir and file if missing."""
    staging = _staging_dir()
    os.makedirs(staging, exist_ok=True)
    staging_file = os.path.join(staging, "session_history.md")

    config = _load_config()
    max_tokens = config.get("max_history_tokens", 3000)
    trim_strategy = config.get("trim_strategy", None)

    records = load_session(_sessions_dir(), session_id)
    records = trim_records(records, max_tokens=max_tokens, trim_strategy=trim_strategy)
    formatted = format_session_history(records)
    with open(staging_file, "w", encoding="utf-8") as f:
        f.write(formatted)


def _clear_staging():
    """Clear all staging files."""
    staging = _staging_dir()
    os.makedirs(staging, exist_ok=True)
    # Clear session history
    staging_file = os.path.join(staging, "session_history.md")
    with open(staging_file, "w", encoding="utf-8") as f:
        f.write("")
    # Clear all agent memory files
    for f in glob.glob(os.path.join(staging, "*_memory.md")):
        with open(f, "w", encoding="utf-8") as fh:
            fh.write("")


# ---------------------------------------------------------------------------
# Active session status
# ---------------------------------------------------------------------------

def _status_file():
    """Path to active_session.json in staging/."""
    return os.path.join(_staging_dir(), "active_session.json")


def _set_status(session_id, round_num=0, agent_id="", agent_name="", status="running"):
    """Write or update the active session status file."""
    os.makedirs(_staging_dir(), exist_ok=True)
    data = {
        "session_id": session_id,
        "status": status,
        "current_round": round_num,
        "current_agent_id": agent_id,
        "current_agent_name": agent_name,
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    with open(_status_file(), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _clear_status():
    """Remove the active session status file."""
    path = _status_file()
    if os.path.exists(path):
        os.remove(path)


def get_status():
    """
    Read current dispatch status.
    Returns dict with session info if running, or {"status": "idle"} if not.
    """
    path = _status_file()
    if not os.path.exists(path):
        return {"status": "idle"}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return {"status": "idle"}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def new_session(project_root):
    """Create a fresh session. Returns session_id."""
    _clear_staging()
    session_id = create_session(_sessions_dir())
    return session_id


def run(user_message, project_root, session_id=None, call_llm_fn=None,
        enable_tools=True, max_tool_loops=10):
    """
    User sends a message, agents discuss for max_rounds.

    Args:
        user_message:   The user's input text
        project_root:   Absolute path to the project root directory
        session_id:     Existing session to continue. If None, creates new.
        call_llm_fn:    Optional override for the LLM call function (for testing).
        enable_tools:   Whether to load and use agent tools (default True).
        max_tool_loops: Max LLM↔tool cycles per agent turn (default 10).

    Returns:
        (session_id, responses) where responses is a list of dicts:
        [{"agent_id": ..., "display_name": ..., "round": ..., "content": ...,
          "tool_history": [...]}, ...]
    """
    config = _load_config()

    # --- Resolve LLM caller ---
    if call_llm_fn is None:
        call_llm_fn = _get_llm_caller(project_root)
    if call_llm_fn is None:
        raise RuntimeError(
            "No LLM caller available. Ensure lib/llm_client.py exists "
            "or pass call_llm_fn for testing."
        )

    # --- Load global config for LLM calls ---
    global_config_path = os.path.join(project_root, "config.json")
    global_config = {}
    if os.path.exists(global_config_path):
        with open(global_config_path, "r", encoding="utf-8") as f:
            global_config = json.load(f)

    # --- Session ---
    if session_id is None:
        session_id = new_session(project_root)

    # --- Append user input ---
    append_to_session(_sessions_dir(), session_id, {
        "type": "user_input",
        "content": user_message,
    })

    # --- Preload tools per agent (once, not per round) ---
    agent_tools = {}
    if enable_tools:
        for agent_id in config.get("turn_order", config["agents"].keys()):
            tool_defs, tool_fns = _load_agent_tools(agent_id, project_root)
            if tool_defs:
                agent_tools[agent_id] = (tool_defs, tool_fns)

    # --- Track cumulative tool history per agent ---
    agent_tool_histories = {}

    # --- Run rounds ---
    max_rounds = config.get("max_rounds", 3)
    turn_order = config.get("turn_order", list(config["agents"].keys()))
    agents = config["agents"]

    responses = []

    for round_num in range(1, max_rounds + 1):
        for agent_id in turn_order:
            agent_conf = agents[agent_id]
            display_name = agent_conf.get("display_name", agent_id)

            # 1. Write current session history to staging
            _write_staging_history(project_root, session_id)

            # 2. Write agent's private memory to staging (from prior rounds)
            if agent_id in agent_tool_histories:
                _write_agent_memory(agent_id, agent_tool_histories[agent_id])

            # 3. Build context for this agent
            instruction = (
                f"This is Round {round_num} of {max_rounds}. "
                f"Respond to the discussion above based on your assigned role."
            )
            messages, provider, model = build_context(
                agent_id=agent_id,
                config=config,
                project_root=project_root,
                user_message=instruction,
            )

            # 4. Call LLM (with tool loop if agent has tools)
            tool_defs, tool_fns = agent_tools.get(agent_id, ([], {}))
            url, key = _resolve_llm_endpoint(provider, global_config)
            content, tool_history = _call_with_tool_loop(
                messages=messages,
                url=url,
                key=key,
                model=model,
                call_llm_fn=call_llm_fn,
                tool_definitions=tool_defs,
                tool_functions=tool_fns,
                max_tool_loops=max_tool_loops,
            )

            # 5. Accumulate tool history for this agent's private memory
            if tool_history:
                if agent_id not in agent_tool_histories:
                    agent_tool_histories[agent_id] = []
                agent_tool_histories[agent_id].extend(tool_history)

            # 6. Record response
            response_record = {
                "type": "agent_response",
                "agent_id": agent_id,
                "display_name": display_name,
                "round": round_num,
                "content": content,
            }
            if tool_history:
                response_record["tool_history"] = tool_history
            append_to_session(_sessions_dir(), session_id, response_record)
            responses.append(response_record)

        # Mark round complete
        append_to_session(_sessions_dir(), session_id, {
            "type": "round_complete",
            "round": round_num,
        })

    return session_id, responses


def run_streaming(user_message, project_root, session_id=None, call_llm_fn=None,
                  enable_tools=True, max_tool_loops=10, max_rounds_override=None):
    """
    Streaming version of run(). Generator that yields events as they happen.

    Yields dicts:
        {"event": "session", "session_id": "..."}
        {"event": "agent_response", "agent_id": ..., "display_name": ...,
         "round": ..., "content": ..., "tools_used": 0}
        {"event": "round_complete", "round": N}
        {"event": "done"}
        {"event": "error", "message": "..."}
    """
    config = _load_config()

    # --- Resolve LLM caller ---
    if call_llm_fn is None:
        call_llm_fn = _get_llm_caller(project_root)
    if call_llm_fn is None:
        yield {"event": "error", "message": "No LLM caller available"}
        return

    # --- Load global config ---
    global_config_path = os.path.join(project_root, "config.json")
    global_config = {}
    if os.path.exists(global_config_path):
        with open(global_config_path, "r", encoding="utf-8") as f:
            global_config = json.load(f)

    # --- Session ---
    if session_id is None:
        session_id = new_session(project_root)

    yield {"event": "session", "session_id": session_id}

    # --- Mark as active ---
    _set_status(session_id, status="running")

    # --- Append user input ---
    append_to_session(_sessions_dir(), session_id, {
        "type": "user_input",
        "content": user_message,
    })

    # --- Preload tools ---
    agent_tools = {}
    if enable_tools:
        for agent_id in config.get("turn_order", config["agents"].keys()):
            tool_defs, tool_fns = _load_agent_tools(agent_id, project_root)
            if tool_defs:
                agent_tools[agent_id] = (tool_defs, tool_fns)

    agent_tool_histories = {}

    # --- Run rounds ---
    max_rounds = config.get("max_rounds", 3) if max_rounds_override is None else max_rounds_override
    turn_order = config.get("turn_order", list(config["agents"].keys()))
    agents = config["agents"]

    for round_num in range(1, max_rounds + 1):
        for agent_id in turn_order:
            agent_conf = agents[agent_id]
            display_name = agent_conf.get("display_name", agent_id)

            try:
                # Update status: which agent is thinking
                _set_status(session_id, round_num, agent_id, display_name, "running")

                _write_staging_history(project_root, session_id)

                if agent_id in agent_tool_histories:
                    _write_agent_memory(agent_id, agent_tool_histories[agent_id])

                instruction = (
                    f"This is Round {round_num} of {max_rounds}. "
                    f"Respond to the discussion above based on your assigned role."
                )
                messages, provider, model = build_context(
                    agent_id=agent_id,
                    config=config,
                    project_root=project_root,
                    user_message=instruction,
                )

                tool_defs, tool_fns = agent_tools.get(agent_id, ([], {}))
                url, key = _resolve_llm_endpoint(provider, global_config)
                content, tool_history = _call_with_tool_loop(
                    messages=messages,
                    url=url,
                    key=key,
                    model=model,
                    call_llm_fn=call_llm_fn,
                    tool_definitions=tool_defs,
                    tool_functions=tool_fns,
                    max_tool_loops=max_tool_loops,
                )

                if tool_history:
                    if agent_id not in agent_tool_histories:
                        agent_tool_histories[agent_id] = []
                    agent_tool_histories[agent_id].extend(tool_history)

                response_record = {
                    "type": "agent_response",
                    "agent_id": agent_id,
                    "display_name": display_name,
                    "round": round_num,
                    "content": content,
                }
                if tool_history:
                    response_record["tool_history"] = tool_history
                append_to_session(_sessions_dir(), session_id, response_record)

                # Yield to frontend immediately
                yield {
                    "event": "agent_response",
                    "agent_id": agent_id,
                    "display_name": display_name,
                    "round": round_num,
                    "content": content,
                    "tools_used": len(tool_history) if tool_history else 0,
                }

            except Exception as e:
                yield {"event": "error", "message": f"{display_name}: {str(e)}"}

        append_to_session(_sessions_dir(), session_id, {
            "type": "round_complete",
            "round": round_num,
        })
        yield {"event": "round_complete", "round": round_num}

    _clear_status()
    yield {"event": "done"}


def get_session_history(project_root, session_id):
    """Load full session records."""
    return load_session(_sessions_dir(), session_id)


def get_all_sessions(project_root):
    """List all session IDs, most recent first."""
    return list_sessions(_sessions_dir())


def remove_session(project_root, session_id):
    """Delete a session."""
    delete_session(_sessions_dir(), session_id)
