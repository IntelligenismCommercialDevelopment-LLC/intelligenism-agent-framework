"""
Context Injector — Context assembly for dispatch collaboration

Reads dispatch_config.json to get the list of context_files for a given
agent, reads each file in order, concatenates them into a system prompt,
resolves provider/model settings, and returns a ready-to-send messages
array.

The injector does not interpret file contents. It does not know whether
a file is a SOUL, a skill, a rule, or session history. It reads files
and concatenates strings. All semantic meaning lives in the files
themselves and in dispatch_config.json.

Output: (messages, provider, model) tuple
"""

import json
import os


def build_context(agent_id, config, project_root, user_message):
    """
    Assemble a complete messages array for one agent in a dispatch call.

    Args:
        agent_id:     Key in config["agents"], e.g. "default" or "agent2"
        config:       The parsed dispatch_config.json dict
        project_root: Absolute path to the project root directory
        user_message: The message to place in the user role (provided by dispatch.py)

    Returns:
        (messages, provider, model) tuple where:
        - messages:  List of dicts ready for call_llm()
        - provider:  Provider string resolved from config
        - model:     Model string resolved from config
    """
    agent_config = config["agents"][agent_id]

    # --- 1. Read context_files in order, concatenate into system prompt ---
    system_parts = []
    for relative_path in agent_config.get("context_files", []):
        full_path = os.path.join(project_root, relative_path)
        content = _read_file(full_path)
        if content:
            system_parts.append(content)

    # --- 2. Append global round info ---
    max_rounds = config.get("max_rounds", 3)
    display_name = agent_config.get("display_name", agent_id)
    global_instruction = (
        f"\n\n---\n"
        f"You are participating in a roundtable discussion as [{display_name}]. "
        f"The discussion runs for {max_rounds} rounds per user message. "
        f"Respond in your assigned role. Address other participants by their names."
    )
    system_parts.append(global_instruction)

    system_prompt = "\n\n".join(system_parts)

    # --- 3. Build messages array ---
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_message},
    ]

    # --- 4. Resolve provider and model ---
    provider, model = _resolve_provider_model(agent_id, agent_config, project_root)

    return messages, provider, model


def _read_file(path):
    """Read a file and return its content. Return empty string if file
    does not exist or is empty."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read().strip()
    except FileNotFoundError:
        return ""
    except Exception as e:
        print(f"[context_injector] Warning: could not read {path}: {e}")
        return ""


def _resolve_provider_model(agent_id, agent_config, project_root):
    """Resolve provider and model. If set to 'from_agent', read from
    the agent's own agent_config.json. Otherwise use the value directly."""
    provider = agent_config.get("provider", "from_agent")
    model = agent_config.get("model", "from_agent")

    if provider == "from_agent" or model == "from_agent":
        agent_conf_path = os.path.join(
            project_root, "agents", agent_id, "agent_config.json"
        )
        try:
            with open(agent_conf_path, "r", encoding="utf-8") as f:
                ac = json.load(f)
            if provider == "from_agent":
                provider = ac.get("provider", "")
            if model == "from_agent":
                model = ac.get("model", "")
        except FileNotFoundError:
            print(
                f"[context_injector] Warning: agent config not found "
                f"at {agent_conf_path}, using empty provider/model"
            )
            if provider == "from_agent":
                provider = ""
            if model == "from_agent":
                model = ""
        except Exception as e:
            print(f"[context_injector] Warning: could not read agent config: {e}")
            if provider == "from_agent":
                provider = ""
            if model == "from_agent":
                model = ""

    return provider, model
