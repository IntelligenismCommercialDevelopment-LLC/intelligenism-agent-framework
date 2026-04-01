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

==========================================================================
STRATEGY AUTHORS: This is the file you modify to create a new dispatch
strategy. The infrastructure code (tool loops, LLM parsing, staging
management) lives in dispatch_base.py — you should not need to touch it.

To create a new strategy:
    1. Copy this entire folder
    2. Modify the run_streaming() function below to implement your
       orchestration pattern (e.g. debate, star topology, serial pipeline)
    3. Update dispatch_config.json with your agents and settings
    4. Update rules/*.md with role definitions
==========================================================================

Entry points:
    new_session(project_root)
    run(user_message, project_root, session_id, ...)
    run_streaming(user_message, project_root, session_id, ...)
    get_session_history(project_root, session_id)
    get_all_sessions(project_root)
    remove_session(project_root, session_id)
    get_status()

Call chain:
    dispatch.py
    ├── dispatch_base (tool loop, LLM parsing, staging, status)
    ├── session_manager (create / append / load / format)
    └── context_injector.build_context() per agent
"""

import os

from dispatch_base import (
    get_llm_caller,
    load_global_config,
    resolve_llm_endpoint,
    load_agent_tools,
    call_with_tool_loop,
    write_agent_memory,
    write_staging_history,
    clear_staging,
    set_status,
    clear_status,
    get_status,
    _load_config,
    _sessions_dir,
)
from session_manager import (
    create_session,
    append_to_session,
    load_session,
    list_sessions,
    delete_session,
)
from context_injector import build_context


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def new_session(project_root):
    """Create a fresh session. Returns session_id."""
    clear_staging()
    session_id = create_session(_sessions_dir())
    return session_id


def run_streaming(user_message, project_root, session_id=None, call_llm_fn=None,
                  enable_tools=True, max_tool_loops=10, max_rounds_override=None):
    """
    Primary entry point. Generator that yields events as agents discuss.

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
        call_llm_fn = get_llm_caller(project_root)
    if call_llm_fn is None:
        yield {"event": "error", "message": "No LLM caller available"}
        return

    # --- Load global config ---
    global_config = load_global_config(project_root)

    # --- Session ---
    if session_id is None:
        session_id = new_session(project_root)

    yield {"event": "session", "session_id": session_id}

    # --- Mark as active ---
    set_status(session_id, status="running")

    # --- Append user input ---
    append_to_session(_sessions_dir(), session_id, {
        "type": "user_input",
        "content": user_message,
    })

    # --- Preload tools per agent (once, not per round) ---
    agent_tools = {}
    if enable_tools:
        for agent_id in config.get("turn_order", config["agents"].keys()):
            tool_defs, tool_fns = load_agent_tools(agent_id, project_root)
            if tool_defs:
                agent_tools[agent_id] = (tool_defs, tool_fns)

    # --- Track cumulative tool history per agent ---
    agent_tool_histories = {}

    # ===================================================================
    # ORCHESTRATION LOGIC — This is the part you modify for new strategies
    # ===================================================================

    max_rounds = config.get("max_rounds", 3) if max_rounds_override is None else max_rounds_override
    turn_order = config.get("turn_order", list(config["agents"].keys()))
    agents = config["agents"]

    for round_num in range(1, max_rounds + 1):
        for agent_id in turn_order:
            agent_conf = agents[agent_id]
            display_name = agent_conf.get("display_name", agent_id)

            try:
                # Update status: which agent is thinking
                set_status(session_id, round_num, agent_id, display_name, "running")

                # 1. Write current session history to staging
                write_staging_history(project_root, session_id)

                # 2. Write agent's private memory to staging (from prior rounds)
                if agent_id in agent_tool_histories:
                    write_agent_memory(agent_id, agent_tool_histories[agent_id])

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
                url, key = resolve_llm_endpoint(provider, global_config)
                content, tool_history = call_with_tool_loop(
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

                # 7. Yield to frontend immediately
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

        # Mark round complete
        append_to_session(_sessions_dir(), session_id, {
            "type": "round_complete",
            "round": round_num,
        })
        yield {"event": "round_complete", "round": round_num}

    # ===================================================================
    # END ORCHESTRATION LOGIC
    # ===================================================================

    clear_status()
    yield {"event": "done"}


def run(user_message, project_root, session_id=None, call_llm_fn=None,
        enable_tools=True, max_tool_loops=10, max_rounds_override=None):
    """
    Non-streaming wrapper around run_streaming().
    Collects all events and returns (session_id, responses).

    Returns:
        (session_id, responses) where responses is a list of dicts:
        [{"agent_id": ..., "display_name": ..., "round": ..., "content": ...,
          "tools_used": ...}, ...]

    Raises:
        RuntimeError: If no LLM caller is available.
    """
    result_session_id = None
    responses = []

    for event in run_streaming(
        user_message=user_message,
        project_root=project_root,
        session_id=session_id,
        call_llm_fn=call_llm_fn,
        enable_tools=enable_tools,
        max_tool_loops=max_tool_loops,
        max_rounds_override=max_rounds_override,
    ):
        evt = event.get("event")

        if evt == "session":
            result_session_id = event["session_id"]

        elif evt == "agent_response":
            responses.append(event)

        elif evt == "error":
            # If no session yet, this is a setup error — raise
            if result_session_id is None:
                raise RuntimeError(event["message"])
            # Otherwise, record the error but continue
            responses.append(event)

    return result_session_id, responses


# ---------------------------------------------------------------------------
# Session helpers
# ---------------------------------------------------------------------------

def get_session_history(project_root, session_id):
    """Load full session records."""
    return load_session(_sessions_dir(), session_id)


def get_all_sessions(project_root):
    """List all session IDs, most recent first."""
    return list_sessions(_sessions_dir())


def remove_session(project_root, session_id):
    """Delete a session."""
    delete_session(_sessions_dir(), session_id)
