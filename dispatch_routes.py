"""
Dispatch Routes — Flask Blueprint for multi-agent collaboration

Provides API endpoints and UI page serving for all dispatch strategies.
Strategies are auto-discovered by scanning the dispatch/ directory.

Register in chat_server.py:
    from dispatch_routes import dispatch_bp
    app.register_blueprint(dispatch_bp)

Routes:
    GET  /dispatch/<strategy>                        → Serve strategy's HTML page
    GET  /api/dispatch                               → List all available strategies
    POST /api/dispatch/<strategy>/sessions           → Create new session
    GET  /api/dispatch/<strategy>/sessions           → List all sessions
    POST /api/dispatch/<strategy>/sessions/<id>/chat → User sends message
    GET  /api/dispatch/<strategy>/sessions/<id>      → Get session history
"""

import json
import os
import sys
import importlib
import importlib.util
from flask import Blueprint, request, jsonify, send_file, abort, Response


# ---------------------------------------------------------------------------
# Blueprint
# ---------------------------------------------------------------------------

dispatch_bp = Blueprint("dispatch", __name__)


def _project_root():
    """Resolve project root. Assumes dispatch_routes.py is in project root."""
    return os.path.dirname(os.path.abspath(__file__))


def _dispatch_base():
    """Absolute path to dispatch/ directory."""
    return os.path.join(_project_root(), "dispatch")


# ---------------------------------------------------------------------------
# Strategy discovery and loading
# ---------------------------------------------------------------------------

def _discover_strategies():
    """
    Scan dispatch/ directory for available strategies.
    A valid strategy is a folder containing dispatch.py.

    Returns:
        List of dicts: [{"name": "roundtable", "display_name": "...",
                         "description": "...", "has_ui": True}, ...]
    """
    base = _dispatch_base()
    if not os.path.isdir(base):
        return []

    strategies = []
    for name in sorted(os.listdir(base)):
        strategy_dir = os.path.join(base, name)
        if not os.path.isdir(strategy_dir):
            continue
        if not os.path.isfile(os.path.join(strategy_dir, "dispatch.py")):
            continue

        # Read display info from config
        config_path = os.path.join(strategy_dir, "dispatch_config.json")
        display_name = name
        description = ""
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    config = json.load(f)
                display_name = config.get("display_name", name)
                description = config.get("description", "")
            except (json.JSONDecodeError, IOError):
                pass

        # Check for HTML page
        has_ui = any(
            f.endswith(".html")
            for f in os.listdir(strategy_dir)
            if os.path.isfile(os.path.join(strategy_dir, f))
        )

        strategies.append({
            "name": name,
            "display_name": display_name,
            "description": description,
            "has_ui": has_ui,
        })

    return strategies


def _load_strategy_module(strategy_name):
    """
    Dynamically import dispatch/{strategy_name}/dispatch.py.
    Returns the module, or None if not found.
    """
    strategy_dir = os.path.join(_dispatch_base(), strategy_name)
    dispatch_file = os.path.join(strategy_dir, "dispatch.py")

    if not os.path.isfile(dispatch_file):
        return None

    # Ensure strategy dir is in sys.path for local imports
    if strategy_dir not in sys.path:
        sys.path.insert(0, strategy_dir)

    module_name = f"dispatch_{strategy_name}"
    spec = importlib.util.spec_from_file_location(module_name, dispatch_file)
    if spec is None:
        return None

    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        print(f"[dispatch_routes] Error loading {strategy_name}: {e}")
        return None

    return module


# ---------------------------------------------------------------------------
# UI Routes
# ---------------------------------------------------------------------------

@dispatch_bp.route("/dispatch/<strategy_name>")
def serve_dispatch_page(strategy_name):
    """Serve the HTML page for a dispatch strategy."""
    strategy_dir = os.path.join(_dispatch_base(), strategy_name)
    if not os.path.isdir(strategy_dir):
        abort(404)

    # Find the first .html file in the strategy folder
    for filename in os.listdir(strategy_dir):
        if filename.endswith(".html"):
            return send_file(os.path.join(strategy_dir, filename))

    abort(404)


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@dispatch_bp.route("/api/dispatch", methods=["GET"])
def list_strategies():
    """List all available dispatch strategies."""
    strategies = _discover_strategies()
    return jsonify({"strategies": strategies})


@dispatch_bp.route("/api/dispatch/<strategy_name>/config", methods=["GET"])
def get_strategy_config(strategy_name):
    """Return the dispatch_config.json for a strategy."""
    config_path = os.path.join(_dispatch_base(), strategy_name, "dispatch_config.json")
    if not os.path.isfile(config_path):
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    try:
        with open(config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        return jsonify(config)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dispatch_bp.route("/api/dispatch/<strategy_name>/status", methods=["GET"])
def get_strategy_status(strategy_name):
    """Return whether a dispatch strategy is currently running."""
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    if not hasattr(module, "get_status"):
        return jsonify({"status": "idle"})

    try:
        return jsonify(module.get_status())
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dispatch_bp.route("/api/dispatch/<strategy_name>/sessions", methods=["POST"])
def create_session(strategy_name):
    """Create a new session for a dispatch strategy."""
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    try:
        session_id = module.new_session(_project_root())
        return jsonify({
            "session_id": session_id,
            "strategy": strategy_name,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dispatch_bp.route("/api/dispatch/<strategy_name>/sessions", methods=["GET"])
def list_sessions(strategy_name):
    """List all sessions for a dispatch strategy."""
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    try:
        sessions = module.get_all_sessions(_project_root())
        return jsonify({
            "strategy": strategy_name,
            "sessions": sessions,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dispatch_bp.route(
    "/api/dispatch/<strategy_name>/sessions/<session_id>/chat",
    methods=["POST"],
)
def chat_in_session(strategy_name, session_id):
    """
    User sends a message in a dispatch session.
    Triggers agents to discuss for max_rounds.

    Request body:
        {"message": "user's input text"}

    Response:
        {
            "session_id": "...",
            "responses": [
                {"agent_id": "...", "display_name": "...", "round": 1,
                 "content": "..."},
                ...
            ]
        }
    """
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' in request body"}), 400

    user_message = data["message"]

    try:
        session_id, responses = module.run(
            user_message=user_message,
            project_root=_project_root(),
            session_id=session_id,
        )

        # Strip tool_history from API response (keep it in JSONL only)
        clean_responses = []
        for r in responses:
            clean = {
                "agent_id": r["agent_id"],
                "display_name": r["display_name"],
                "round": r["round"],
                "content": r["content"],
            }
            if "tool_history" in r:
                clean["tools_used"] = len(r["tool_history"])
            clean_responses.append(clean)

        return jsonify({
            "session_id": session_id,
            "responses": clean_responses,
        })
    except FileNotFoundError:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dispatch_bp.route(
    "/api/dispatch/<strategy_name>/sessions/<session_id>/chat/stream",
    methods=["POST"],
)
def chat_in_session_stream(strategy_name, session_id):
    """
    SSE streaming version of chat. Each agent response is pushed
    as a Server-Sent Event as soon as it's generated.

    Request body:
        {"message": "user's input text"}

    SSE events:
        event: session      data: {"session_id": "..."}
        event: agent_response  data: {"agent_id": ..., "display_name": ..., ...}
        event: round_complete  data: {"round": N}
        event: error        data: {"message": "..."}
        event: done         data: {}
    """
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    if not hasattr(module, "run_streaming"):
        return jsonify({"error": "Strategy does not support streaming"}), 400

    data = request.get_json()
    if not data or "message" not in data:
        return jsonify({"error": "Missing 'message' in request body"}), 400

    user_message = data["message"]
    max_rounds = data.get("max_rounds", None)

    def generate():
        try:
            kwargs = {
                "user_message": user_message,
                "project_root": _project_root(),
                "session_id": session_id,
            }
            if max_rounds is not None:
                kwargs["max_rounds_override"] = int(max_rounds)

            for event in module.run_streaming(**kwargs):
                event_type = event.get("event", "message")
                event_data = json.dumps(event, ensure_ascii=False)
                yield f"event: {event_type}\ndata: {event_data}\n\n"
        except Exception as e:
            error_data = json.dumps({"event": "error", "message": str(e)})
            yield f"event: error\ndata: {error_data}\n\n"

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@dispatch_bp.route(
    "/api/dispatch/<strategy_name>/sessions/<session_id>",
    methods=["GET"],
)
def get_session(strategy_name, session_id):
    """Get full session history."""
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    try:
        records = module.get_session_history(_project_root(), session_id)
        return jsonify({
            "session_id": session_id,
            "strategy": strategy_name,
            "records": records,
        })
    except FileNotFoundError:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@dispatch_bp.route(
    "/api/dispatch/<strategy_name>/sessions/<session_id>",
    methods=["DELETE"],
)
def delete_session_route(strategy_name, session_id):
    """Delete a session."""
    module = _load_strategy_module(strategy_name)
    if module is None:
        return jsonify({"error": f"Strategy '{strategy_name}' not found"}), 404

    try:
        module.remove_session(_project_root(), session_id)
        return jsonify({"status": "ok", "session_id": session_id})
    except FileNotFoundError:
        return jsonify({"error": f"Session '{session_id}' not found"}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500
