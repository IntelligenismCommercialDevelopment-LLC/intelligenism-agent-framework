"""
Chat server — routes requests to the correct agent's Fundamental Loop.
Dynamically discovers agents by scanning the agents/ directory.
Serves the index (yellow pages) and all page-level HTML files.
"""

import sys
import os
import json
import threading
import importlib.util

FRAMEWORK_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, FRAMEWORK_ROOT)

from flask import Flask, request, jsonify, abort
from dispatch_routes import dispatch_bp
from tube_routes import tube_bp, set_tube_runner
from tube.tube_runner import TubeRunner

app = Flask(__name__)
app.register_blueprint(dispatch_bp)
app.register_blueprint(tube_bp)
AGENTS_DIR = os.path.join(FRAMEWORK_ROOT, "agents")
PAGES_DIR = os.path.join(FRAMEWORK_ROOT, "pages")


# ==============================
# Agent dynamic loading
# ==============================

def _load_agent_engine(agent_id):
    """Dynamically load an agent's direct_llm module."""
    agent_dir = os.path.join(AGENTS_DIR, agent_id)
    engine_path = os.path.join(agent_dir, "core", "direct_llm.py")

    if not os.path.exists(engine_path):
        raise FileNotFoundError(f"Agent '{agent_id}' not found at {engine_path}")

    # Add agent's directory to path so its internal imports work
    if agent_dir not in sys.path:
        sys.path.insert(0, agent_dir)

    spec = importlib.util.spec_from_file_location(f"agent_{agent_id}_engine", engine_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    # Restore path
    if agent_dir in sys.path:
        sys.path.remove(agent_dir)

    return module


def _discover_agents():
    """Scan agents/ directory and return list of agent info dicts."""
    agents = []
    if not os.path.exists(AGENTS_DIR):
        return agents

    for name in sorted(os.listdir(AGENTS_DIR)):
        agent_dir = os.path.join(AGENTS_DIR, name)
        config_path = os.path.join(agent_dir, "agent_config.json")
        if os.path.isdir(agent_dir) and os.path.exists(config_path):
            with open(config_path, "r", encoding="utf-8") as f:
                config = json.load(f)
            agents.append({
                "id": name,
                "display_name": config.get("display_name", name),
                "model": config.get("model", "unknown"),
            })
    return agents


def _discover_pages():
    """Scan pages/ directory and return list of user-created pages."""
    pages = []

    # Built-in pages (always present)
    pages.append({
        "id": "chat",
        "name": "Chat",
        "description": "Single-agent chat interface",
        "url": "/chat",
        "built_in": True,
    })

    # User-created pages in pages/ directory
    if os.path.exists(PAGES_DIR):
        for filename in sorted(os.listdir(PAGES_DIR)):
            if filename.endswith(".html"):
                page_id = filename[:-5]  # strip .html

                # Try to read page metadata from companion .json file
                meta_path = os.path.join(PAGES_DIR, f"{page_id}.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                else:
                    meta = {}

                pages.append({
                    "id": page_id,
                    "name": meta.get("name", page_id),
                    "description": meta.get("description", ""),
                    "url": f"/pages/{page_id}",
                    "built_in": False,
                })

    return pages


# ==============================
# Routes — Page serving
# ==============================

@app.route('/')
def index():
    """Serve the index / yellow pages."""
    index_path = os.path.join(FRAMEWORK_ROOT, "index.html")
    with open(index_path, "r", encoding="utf-8") as f:
        return f.read()


@app.route('/chat')
def chat_page():
    """Serve the chat interface."""
    chat_path = os.path.join(FRAMEWORK_ROOT, "chat.html")
    with open(chat_path, "r", encoding="utf-8") as f:
        return f.read()


@app.route('/pages/<page_name>')
def user_page(page_name):
    """Serve user-created pages from pages/ directory."""
    # Security: prevent path traversal
    if '/' in page_name or '\\' in page_name or '..' in page_name:
        abort(400)

    page_path = os.path.join(PAGES_DIR, f"{page_name}.html")
    if not os.path.exists(page_path):
        abort(404)

    with open(page_path, "r", encoding="utf-8") as f:
        return f.read()


# ==============================
# Routes — API
# ==============================

@app.route('/api/pages')
def api_pages():
    """Return list of all available pages (built-in + user-created)."""
    return jsonify(_discover_pages())


@app.route('/api/agents')
def api_agents():
    """Return list of discovered agents."""
    return jsonify(_discover_agents())


@app.route('/api/chat', methods=['POST'])
def api_chat():
    """Send message to an agent."""
    data = request.json
    agent_id = data.get("agent_id", "")
    message = data.get("message", "")

    if not agent_id or not message:
        return jsonify({"error": "agent_id and message required"}), 400

    try:
        engine = _load_agent_engine(agent_id)
        reply = engine.call_agent(message, mode="chat")
        return jsonify({"reply": reply})
    except FileNotFoundError as e:
        return jsonify({"error": str(e)}), 404
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/history/<agent_id>')
def api_history(agent_id):
    """Get an agent's chat history."""
    try:
        engine = _load_agent_engine(agent_id)
        history = engine.get_history()
        return jsonify(history)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/history/<agent_id>', methods=['DELETE'])
def api_clear(agent_id):
    """Clear an agent's chat history."""
    try:
        engine = _load_agent_engine(agent_id)
        engine.clear_history()
        return jsonify({"status": "ok"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    agents = _discover_agents()
    pages = _discover_pages()
    print(f"Discovered {len(agents)} agent(s): {[a['display_name'] for a in agents]}")
    print(f"Discovered {len(pages)} page(s): {[p['name'] for p in pages]}")

    # Ensure pages/ directory exists
    os.makedirs(PAGES_DIR, exist_ok=True)

    # Start Tube Runner in background thread
    tube_runner = TubeRunner(interval=15)
    set_tube_runner(tube_runner)
    tube_thread = threading.Thread(target=tube_runner.run, daemon=True,
                                   name="tube-runner")
    tube_thread.start()

    print("Index running at http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=True)
