"""
Template Dispatch — Backend dispatch unit for one agent.

Public API:
    TemplateDispatch(agent_dir) — Initialize dispatch for one agent
        .new_session(session_id=None) — Start a new session
        .run(retry=False) -> dict   — Execute one dispatch call
        .append_to_history(role, content) — Inject messages into history
        .close_session()            — Archive and close current session
"""

import json
import os
import sys
import time

# Bring dispatch directory onto sys.path so sibling imports work
_DISPATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_ROOT = os.path.dirname(os.path.dirname(_DISPATCH_DIR))
if _DISPATCH_DIR not in sys.path:
    sys.path.insert(0, _DISPATCH_DIR)

from dispatch_base import (
    get_llm_caller,
    load_global_config,
    resolve_llm_endpoint,
    call_with_tool_loop,
)
import session_io
import runtime

# Fix dispatch_base path resolution for THIS dispatch directory.
import dispatch_base as _dispatch_base_module
_dispatch_base_module._DISPATCH_DIR = _DISPATCH_DIR


# ---------------------------------------------------------------------------
# Context path resolution — supports "session:" prefix
# ---------------------------------------------------------------------------

def _resolve_context_path(dispatch_dir, path, sid):
    """
    Resolve a context_files entry to an absolute path.

    Convention:
      - Path with "session:" prefix -> relative to sessions/{sid}/
      - Plain path -> relative to dispatch directory
      - If sid is None and path has "session:" prefix, returns None.
    """
    if path.startswith("session:"):
        if not sid:
            return None
        rel = path[len("session:"):]
        return os.path.join(session_io.session_dir(dispatch_dir, sid), rel)
    return os.path.join(dispatch_dir, path)


def _read_context_file(dispatch_dir, path, sid):
    """Read a context file. Returns empty string if missing or empty."""
    full = _resolve_context_path(dispatch_dir, path, sid)
    if full is None:
        return ""
    try:
        with open(full, "r", encoding="utf-8") as f:
            return f.read().strip()
    except (FileNotFoundError, IOError):
        return ""


def _build_context(dispatch_dir, context_files, sid):
    """
    Read context_files and split:
      - Regular files -> system prompt text
      - _tools.json files -> tool definitions for LLM tools parameter

    Returns:
        (system_prompt: str, external_tools: list[dict])
    """
    system_parts = []
    external_tools = []

    for path in context_files:
        content = _read_context_file(dispatch_dir, path, sid)
        if not content:
            continue

        # Route: _tools.json -> tools parameter
        resolved = _resolve_context_path(dispatch_dir, path, sid)
        if resolved and resolved.endswith("_tools.json"):
            try:
                tool_defs = json.loads(content)
                if isinstance(tool_defs, list):
                    external_tools.extend(tool_defs)
                else:
                    external_tools.append(tool_defs)
            except json.JSONDecodeError:
                pass
        else:
            system_parts.append(content)

    system = "\n\n---\n\n".join(system_parts) if system_parts else "You are a helpful assistant."
    return system, external_tools


# ---------------------------------------------------------------------------
# History loading — excludes compacted rounds
# ---------------------------------------------------------------------------

def _load_conversation_history(dispatch_dir, sid, max_turns=50):
    """Load history messages, excluding rounds covered by trimmed_marker."""
    if not sid:
        return []
    records = session_io.load_history(dispatch_dir, sid)

    compacted_rounds = set()
    for rec in records:
        if rec.get("type") == "trimmed_marker":
            for r in rec.get("compacted_rounds", []):
                compacted_rounds.add(r)

    messages = []
    round_num = 0
    i = 0
    conv_records = [r for r in records if r.get("type") in ("user_input", "agent_response")]
    while i < len(conv_records):
        rec = conv_records[i]
        if rec.get("type") == "user_input":
            round_num += 1
            if round_num in compacted_rounds:
                i += 1
                if i < len(conv_records) and conv_records[i].get("type") == "agent_response":
                    i += 1
                continue
            messages.append({"role": "user", "content": rec.get("content", "")})
            if i + 1 < len(conv_records) and conv_records[i + 1].get("type") == "agent_response":
                messages.append({"role": "assistant", "content": conv_records[i + 1].get("content", "")})
                i += 2
            else:
                i += 1
        else:
            i += 1

    return messages[-max_turns * 2:]


# ---------------------------------------------------------------------------
# Tool loading — local tools from tools/*.py
# ---------------------------------------------------------------------------

def _load_dispatch_tools(dispatch_dir):
    """Load tools from dispatch's tools/*_tools.py directory."""
    import glob
    import importlib.util

    tools_dir = os.path.join(dispatch_dir, "tools")
    if not os.path.isdir(tools_dir):
        return [], {}

    tool_definitions = []
    tool_functions = {}

    for filepath in sorted(glob.glob(os.path.join(tools_dir, "*_tools.py"))):
        module_name = os.path.basename(filepath)[:-3]
        spec = importlib.util.spec_from_file_location(module_name, filepath)
        if spec is None:
            continue
        module = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(module)
        except Exception as e:
            print(f"[template dispatch] Could not load {filepath}: {e}")
            continue

        tools_dict = getattr(module, "TOOLS", None)
        if not tools_dict:
            continue

        for tool_name, tool_info in tools_dict.items():
            fn = tool_info.get("handler") or tool_info.get("function")
            if fn is None:
                continue
            tool_functions[tool_name] = fn
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
# External tool handler — for external tools defined in _tools.json
# ---------------------------------------------------------------------------

def _make_external_tool_handler(tool_name):
    """Create a handler for external tools (declarative actions) that acknowledges
    the call without executing anything. The Engine interprets these."""
    def handler(args):
        return json.dumps(
            {"status": "accepted", "tool": tool_name, "input": args},
            ensure_ascii=False,
        )
    return handler


# ---------------------------------------------------------------------------
# Reasoning data saving
# ---------------------------------------------------------------------------

_reasoning_file_cache = {}


def _get_reasoning_filepath(output_dir, step_name):
    """同 step 同批次复用文件名，确保多 agent 的记录 append 到同一文件。"""
    if step_name not in _reasoning_file_cache:
        ts = time.strftime("%y_%m_%d_%H%M%S")
        _reasoning_file_cache[step_name] = os.path.join(
            output_dir, f"{step_name}_{ts}.json"
        )
    return _reasoning_file_cache[step_name]


def _save_reasoning_if_configured(response_meta, reasoning_config,
                                   reasoning_output_path, agent_dir,
                                   agent_conf, config):
    """保存 reasoning 输出到 JSON 文件（append 模式）。
    reasoning_output_path 为 None 时跳过。"""
    if not reasoning_output_path:
        return

    usage = response_meta.get("usage", {})

    # 从 agent_dir 推导 step 名：.../4_score/dispatch/{name}/ → "4_score"
    step_name = os.path.basename(os.path.dirname(os.path.dirname(agent_dir)))

    # 解析 reasoning_tokens（兼容不同响应格式）
    details = usage.get("completion_tokens_details")
    reasoning_tokens = 0
    if isinstance(details, dict):
        reasoning_tokens = details.get("reasoning_tokens", 0)

    record = {
        "step": step_name,
        "agent_id": config.get("agent_id", ""),
        "module": "script_writer",
        "model": agent_conf.get("model", ""),
        "reasoning_effort": (reasoning_config or {}).get("effort", ""),
        "reasoning_text": "",
        "usage": {
            "input_tokens": usage.get("prompt_tokens", 0),
            "output_tokens": usage.get("completion_tokens", 0),
            "reasoning_tokens": reasoning_tokens,
        },
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }

    filepath = _get_reasoning_filepath(reasoning_output_path, step_name)

    # append 到 JSON 数组
    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, "r", encoding="utf-8") as f:
                existing = json.load(f)
        except (json.JSONDecodeError, IOError):
            existing = []
    existing.append(record)
    os.makedirs(os.path.dirname(filepath) if os.path.dirname(filepath) else ".", exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# TemplateDispatch
# ---------------------------------------------------------------------------

class TemplateDispatch:
    """Pure backend dispatch unit for one agent.

    Usage:
        disp = TemplateDispatch("dispatch/template")
        disp.new_session("session_001")
        disp.append_to_history("user", "... input / instructions ...")
        result = disp.run()
        print(result["tool_calls"])
        disp.close_session()
    """

    def __init__(self, agent_dir: str):
        """
        Initialize dispatch instance.

        Args:
            agent_dir: Path to the dispatch directory,
                       e.g. 'IAF_AI/dispatch/template'
        """
        self.agent_dir = os.path.abspath(agent_dir)
        self.project_root = os.path.dirname(os.path.dirname(self.agent_dir))
        self.config = self._load_config()
        self.session_id = None

        # Fix path resolution for all sibling modules
        self._fix_sibling_paths()

    def _fix_sibling_paths(self):
        """Override _DISPATCH_DIR in all cached sibling modules.

        When multiple TemplateDispatch instances coexist, Python's module cache
        causes all agents to share the same sibling modules (session_io, etc.).
        This method patches _DISPATCH_DIR so each agent writes to its own directory.
        """
        _dispatch_base_module._DISPATCH_DIR = self.agent_dir
        for mod_name in ('session_io', 'session_close', 'runtime'):
            mod = sys.modules.get(mod_name)
            if mod and hasattr(mod, '_DISPATCH_DIR'):
                mod._DISPATCH_DIR = self.agent_dir
            if mod and hasattr(mod, '_PROJECT_ROOT'):
                mod._PROJECT_ROOT = self.project_root

    def _load_config(self):
        config_path = os.path.join(self.agent_dir, "dispatch_config.json")
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)

    def new_session(self, session_id: str = None, agent_name: str = "core_chat"):
        """
        Start a new session.
        Clears staging, creates new session record.

        Returns:
            str: The session_id
        """
        self._fix_sibling_paths()
        agent_conf = self.config["agents"][agent_name]
        model = agent_conf.get("model", "")
        provider = agent_conf.get("provider", "")
        self.session_id = session_io.new_session(
            self.agent_dir, model=model, provider=provider,
        )
        return self.session_id

    def run(self, retry: bool = False, agent_name: str = "core_chat") -> dict:
        """
        Execute one dispatch call.

        1. Read config's context_files (split: text -> system, _tools.json -> tools)
        2. Assemble prompt (context_files content + conversation history)
        3. Call LLM with tool loop
        4. Return tool call results

        Args:
            retry: If True, append a retry hint to history before calling LLM

        Returns:
            dict: {
                "tool_calls": [{"name": "submit_action", "input": {"target": "..."}}],
                "text_response": "...",
                "duration_ms": N,
            }
        """
        if not self.session_id:
            raise RuntimeError("No active session. Call new_session() first.")

        self._fix_sibling_paths()
        config = self.config
        sid = self.session_id

        # --- Resolve LLM caller ---
        call_llm_fn = get_llm_caller(self.project_root)
        if call_llm_fn is None:
            raise RuntimeError("No LLM caller available")

        # --- Resolve endpoint ---
        global_config = load_global_config(self.project_root)
        agent_conf = config["agents"][agent_name]
        provider = agent_conf.get("provider", "anthropic")
        model = agent_conf.get("model", "")
        temperature = agent_conf.get("temperature")
        top_p = agent_conf.get("top_p")
        reasoning = agent_conf.get("reasoning")
        reasoning_output_path = agent_conf.get("reasoning_data_output_path")
        max_tokens = agent_conf.get("max_tokens")
        url, key = resolve_llm_endpoint(provider, global_config)

        # --- Retry hint ---
        if retry:
            session_io.append_history(self.agent_dir, sid, {
                "type": "user_input",
                "content": "Your previous response did not include the required tool call. "
                           "Please use the available tools to submit your action.",
            })

        # --- Compaction check (before building context) ---
        compaction_config = config.get("compaction", {})
        if compaction_config.get("enabled") and sid:
            try:
                import compaction as _compaction_mod
                comp_result = _compaction_mod.check_and_trigger(sid, config)
                if comp_result.compacted:
                    session_io.log_call(
                        self.agent_dir, sid, "compaction",
                        file_version=comp_result.file_version,
                        rounds_compressed=len(comp_result.compacted_rounds),
                    )
            except Exception as e:
                print(f"[dispatch] compaction check error: {e}")

        # --- Build context: system prompt + external tool definitions ---
        context_files = agent_conf.get("context_files", [])
        system_prompt, external_tools = _build_context(
            self.agent_dir, context_files, sid,
        )

        history = _load_conversation_history(self.agent_dir, sid)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(history)

        # --- Load tools (mode-dependent) ---
        free_search = agent_conf.get("free_search")

        if free_search and free_search.get("free_search_path"):
            # ═══ FREE SEARCH MODE ═══
            # Load explore_tools.py with path fencing via factory
            import importlib.util as _ilu
            explore_path = os.path.join(self.agent_dir, "tools", "explore_tools.py")
            _spec = _ilu.spec_from_file_location("explore_tools", explore_path)
            _mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_mod)

            read_roots = [os.path.realpath(p)
                          for p in free_search["free_search_path"]]
            write_root = os.path.realpath(free_search.get("output_path", ""))
            if write_root:
                os.makedirs(write_root, exist_ok=True)

            explore_dict = _mod.make_tools(read_roots, write_root)
            tool_definitions = []
            tool_functions = {}
            for tname, tinfo in explore_dict.items():
                tool_functions[tname] = tinfo["handler"]
                tool_definitions.append({
                    "type": "function",
                    "function": {
                        "name": tname,
                        "description": tinfo["description"],
                        "parameters": tinfo["parameters"],
                    },
                })

            tc = free_search.get("tool_choice", "auto")
            max_loops = free_search.get("max_tool_loops", 40)
            exit_tools = {"write_file"} if free_search.get("exit_on_write", True) else None

        else:
            # ═══ STANDARD MODE (TCR / chat / interview) ═══
            tool_definitions, tool_functions = _load_dispatch_tools(self.agent_dir)

            # Register external tools with acknowledgment handlers
            for ext_tool in external_tools:
                func_info = ext_tool.get("function", {})
                name = func_info.get("name", "")
                if name and name not in tool_functions:
                    tool_functions[name] = _make_external_tool_handler(name)
                    tool_definitions.append(ext_tool)

            # Determine tool_choice based on provider
            # Only force tool use when external action tools are present
            tc = None
            if external_tools and tool_definitions:
                if provider in ("openrouter", "openai", "xai"):
                    tc = "required"
                elif provider == "anthropic":
                    tc = {"type": "any"}

            exit_tools = ({t["function"]["name"] for t in external_tools}
                          if external_tools else None)
            max_loops = 10

        # --- Set runtime context ---
        runtime.clear_events()
        runtime.set_current_sid(sid)

        session_io.log_call(self.agent_dir, sid,
                            "call_started", model=model, provider=provider)

        t0 = time.time()
        tool_history = []
        response_meta = {}
        try:
            content, tool_history, response_meta = call_with_tool_loop(
                messages=messages,
                url=url, key=key, model=model,
                call_llm_fn=call_llm_fn,
                tool_definitions=tool_definitions,
                tool_functions=tool_functions,
                max_tool_loops=max_loops,
                tool_choice=tc,
                exit_on_tools=exit_tools,
                temperature=temperature,
                top_p=top_p,
                reasoning=reasoning,
                max_tokens=max_tokens,
            )
        finally:
            runtime.drain_events()
            runtime.clear_current_sid()

        duration_ms = int((time.time() - t0) * 1000)

        session_io.log_call(self.agent_dir, sid, "call_completed",
                            duration_ms=duration_ms,
                            reply_length=len(content),
                            tool_count=len(tool_history))

        # Record agent response in history
        session_io.append_history(self.agent_dir, sid, {
            "type": "agent_response",
            "agent_id": config.get("agent_id", "agent_001"),
            "display_name": agent_conf.get("display_name", "Agent"),
            "round": 1,
            "content": content,
            "tool_count": len(tool_history),
        })

        # --- Save reasoning data if configured ---
        _save_reasoning_if_configured(
            response_meta=response_meta,
            reasoning_config=reasoning,
            reasoning_output_path=reasoning_output_path,
            agent_dir=self.agent_dir,
            agent_conf=agent_conf,
            config=config,
        )

        # --- Build structured result ---
        # Only return external action tools to the engine; filter out internal file tools.
        tool_calls = []
        for th in tool_history:
            if not exit_tools or th["tool"] in exit_tools:
                tool_calls.append({
                    "name": th["tool"],
                    "input": th["input"],
                })

        return {
            "tool_calls": tool_calls,
            "text_response": content,
            "duration_ms": duration_ms,
        }

    def append_to_history(self, role: str, content: str):
        """
        Append a message to conversation history.
        Engine uses this to inject state/input before calling run().

        Args:
            role: "user" or "assistant"
            content: Message content
        """
        if not self.session_id:
            raise RuntimeError("No active session.")

        self._fix_sibling_paths()
        if role == "user":
            session_io.append_history(self.agent_dir, self.session_id, {
                "type": "user_input",
                "content": content,
            })
        else:
            session_io.append_history(self.agent_dir, self.session_id, {
                "type": "agent_response",
                "agent_id": self.config.get("agent_id", "agent_001"),
                "content": content,
            })

    def close_session(self):
        """
        Close current session.
        Triggers: personal memory updates (experiences, relationships, emotional_state).
        """
        if not self.session_id:
            return
        try:
            import session_close
            self._fix_sibling_paths()
            for _event in session_close.close(self.session_id, self.project_root):
                pass  # consume generator events silently
        except Exception as e:
            print(f"[template dispatch] Session close error: {e}")
        finally:
            self.session_id = None

    # ------------------------------------------------------------------
    # Convenience methods for pipeline stages
    # ------------------------------------------------------------------

    def run_single_message(self, user_message: str, agent_name: str = "core_chat") -> dict:
        """
        Single-turn LLM call: new_session → inject user message → run → close.

        Args:
            user_message: The user prompt
            agent_name:   Which agent config to use (default: "core_chat")

        Returns:
            dict with "text_response", "tool_calls", "duration_ms"
        """
        self.new_session(agent_name=agent_name)
        self.append_to_history("user", user_message)
        result = self.run(agent_name=agent_name)
        self.close_session()
        return result

    def run_with_tools(
        self,
        user_message: str,
        tool_definitions: list,
        tool_handler,
        exit_tools: set = None,
        max_tool_loops: int = 30,
        agent_name: str = "core_chat",
    ) -> dict:
        """
        Single-turn LLM call with custom tool definitions and handlers.

        Bypasses the dispatch's normal tool loading to inject
        pipeline-specific tools (submit_segment, submit_visual, etc.).

        Args:
            user_message:     User prompt
            tool_definitions: List of tool definition dicts (OpenAI format)
            tool_handler:     Callable(tool_name, params) -> str
            exit_tools:       Set of tool names that trigger early exit (None = run until done)
            max_tool_loops:   Maximum LLM call cycles (default 30)
            agent_name:       Which agent config to use (default: "core_chat")

        Returns:
            dict with "text_response", "tool_calls", "tool_history", "duration_ms"
        """
        self.new_session(agent_name=agent_name)
        self._fix_sibling_paths()

        config = self.config
        agent_conf = config["agents"][agent_name]

        # Build system prompt from context_files
        context_files = agent_conf.get("context_files", [])
        system_prompt, _ = _build_context(
            self.agent_dir, context_files, self.session_id,
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_message},
        ]

        # Resolve LLM
        call_llm_fn = get_llm_caller(self.project_root)
        if call_llm_fn is None:
            raise RuntimeError("No LLM caller available")

        global_config = load_global_config(self.project_root)
        provider = agent_conf.get("provider", "anthropic")
        model = agent_conf.get("model", "")
        temperature = agent_conf.get("temperature")
        top_p = agent_conf.get("top_p")
        reasoning = agent_conf.get("reasoning")
        reasoning_output_path = agent_conf.get("reasoning_data_output_path")
        max_tokens = agent_conf.get("max_tokens")
        url, key = resolve_llm_endpoint(provider, global_config)

        # Build tool functions from handler
        custom_tool_functions = {}
        for td in tool_definitions:
            name = td["function"]["name"]
            custom_tool_functions[name] = lambda input_data, n=name: tool_handler(n, input_data)

        # Determine tool_choice
        tc = None
        if tool_definitions:
            if provider in ("openrouter", "openai", "xai"):
                tc = "required"
            elif provider == "anthropic":
                tc = {"type": "any"}

        t0 = time.time()
        content, tool_history, response_meta = call_with_tool_loop(
            messages=messages,
            url=url, key=key, model=model,
            call_llm_fn=call_llm_fn,
            tool_definitions=tool_definitions,
            tool_functions=custom_tool_functions,
            max_tool_loops=max_tool_loops,
            tool_choice=tc,
            exit_on_tools=exit_tools,
            temperature=temperature,
            top_p=top_p,
            reasoning=reasoning,
            max_tokens=max_tokens,
        )
        duration_ms = int((time.time() - t0) * 1000)

        # --- Save reasoning data if configured ---
        _save_reasoning_if_configured(
            response_meta=response_meta,
            reasoning_config=reasoning,
            reasoning_output_path=reasoning_output_path,
            agent_dir=self.agent_dir,
            agent_conf=agent_conf,
            config=self.config,
        )

        self.close_session()

        tool_calls = [
            {"name": th["tool"], "input": th["input"]}
            for th in tool_history
        ]

        return {
            "tool_calls": tool_calls,
            "text_response": content,
            "tool_history": tool_history,
            "duration_ms": duration_ms,
        }
