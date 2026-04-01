"""
Tube Routes — Flask Blueprint for tube management and manual triggering.

Register in chat_server.py:
    from tube_routes import tube_bp, set_tube_runner
    app.register_blueprint(tube_bp)
    set_tube_runner(tube_runner)

Routes:
    GET  /api/tubes              → List all tubes with running/idle status
    GET  /api/tube/status        → Real-time status of all tubes
    POST /api/tube/trigger       → Manually trigger a tube (human, AI, or curl)
    GET  /api/tube/log           → Read tube execution log (tail N entries)
"""

import json
import os
from flask import Blueprint, request, jsonify

tube_bp = Blueprint("tube", __name__)

# Module-level reference to the TubeRunner instance.
# Set by chat_server.py at startup via set_tube_runner().
_runner = None


def set_tube_runner(runner):
    """Inject the TubeRunner instance so routes can query its state."""
    global _runner
    _runner = runner


def _project_root():
    return os.path.dirname(os.path.abspath(__file__))


def _tube_dir():
    return os.path.join(_project_root(), "tube")


# ---------------------------------------------------------------------------
# API Routes
# ---------------------------------------------------------------------------

@tube_bp.route("/api/tubes", methods=["GET"])
def list_tubes():
    """Return all tube definitions from tubes.json, with running/idle status."""
    tubes_path = os.path.join(_tube_dir(), "tubes.json")
    if not os.path.isfile(tubes_path):
        return jsonify({"tubes": []})

    try:
        with open(tubes_path, "r", encoding="utf-8") as f:
            tubes = json.load(f)

        # Attach runtime status
        for tube in tubes:
            tid = tube.get("id", "")
            if _runner is not None:
                with _runner.lock:
                    tube["status"] = "running" if tid in _runner.running_tubes else "idle"
            else:
                tube["status"] = "unknown"

        return jsonify({"tubes": tubes})
    except (json.JSONDecodeError, IOError) as e:
        return jsonify({"error": str(e)}), 500


@tube_bp.route("/api/tube/status", methods=["GET"])
def tube_status():
    """
    Return real-time status of all tubes.

    Response:
        {"tubes": [
            {"id": "morning_news", "enabled": true, "status": "running"},
            {"id": "doc_pipeline", "enabled": false, "status": "idle"},
            ...
        ]}
    """
    tubes_path = os.path.join(_tube_dir(), "tubes.json")
    if not os.path.isfile(tubes_path):
        return jsonify({"tubes": []})

    try:
        with open(tubes_path, "r", encoding="utf-8") as f:
            tubes = json.load(f)

        status_list = []
        for tube in tubes:
            tid = tube.get("id", "")
            enabled = tube.get("enabled", True)

            if _runner is not None:
                with _runner.lock:
                    running = tid in _runner.running_tubes
            else:
                running = False

            status_list.append({
                "id": tid,
                "enabled": enabled,
                "status": "running" if running else "idle",
            })

        return jsonify({"tubes": status_list})
    except (json.JSONDecodeError, IOError) as e:
        return jsonify({"error": str(e)}), 500


@tube_bp.route("/api/tube/trigger", methods=["POST"])
def trigger_tube():
    """
    Manually trigger a tube by writing a flag file.

    Request body:
        {"tube_id": "morning_news"}

    Works for humans (UI button), AI agents (curl), or any HTTP client.
    The tube_runner picks up the flag file on its next scan cycle.
    """
    data = request.get_json()
    if not data or "tube_id" not in data:
        return jsonify({"error": "Missing 'tube_id' in request body"}), 400

    tube_id = data["tube_id"]

    # Validate tube exists
    tubes_path = os.path.join(_tube_dir(), "tubes.json")
    if os.path.isfile(tubes_path):
        try:
            with open(tubes_path, "r", encoding="utf-8") as f:
                tubes = json.load(f)
            if not any(t.get("id") == tube_id for t in tubes):
                return jsonify({"error": f"Tube '{tube_id}' not found"}), 404
        except (json.JSONDecodeError, IOError):
            pass

    # Write flag file
    flag_dir = os.path.join(_tube_dir(), "manual_triggers")
    os.makedirs(flag_dir, exist_ok=True)
    flag_path = os.path.join(flag_dir, f"{tube_id}.flag")

    with open(flag_path, "w", encoding="utf-8") as f:
        f.write(tube_id)

    return jsonify({"status": "triggered", "tube_id": tube_id})


@tube_bp.route("/api/tube/log", methods=["GET"])
def get_tube_log():
    """
    Return recent tube execution log entries.

    Query params:
        tail (int): Number of most recent entries to return (default: 50)
        tube_id (str): Filter by specific tube (optional)
    """
    log_path = os.path.join(_tube_dir(), "tube_log.jsonl")
    if not os.path.isfile(log_path):
        return jsonify({"entries": []})

    tail = request.args.get("tail", 50, type=int)
    filter_id = request.args.get("tube_id", None)

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Filter first, then tail — so "last 10 of morning_news" works
        # even if other tubes have thousands of recent entries
        all_entries = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            if filter_id and entry.get("tube_id") != filter_id:
                continue
            all_entries.append(entry)

        return jsonify({"entries": all_entries[-tail:]})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@tube_bp.route("/api/tube/log/grouped", methods=["GET"])
def get_tube_log_grouped():
    """
    Return recent log entries grouped by tube_id. One request, all tubes.

    Query params:
        per_tube (int): Max entries per tube (default: 10)

    Response:
        {"groups": {
            "morning_news": [...last 10 entries...],
            "doc_pipeline": [...last 10 entries...],
            "_system": [...runner_started/stopped events...]
        }}
    """
    log_path = os.path.join(_tube_dir(), "tube_log.jsonl")
    if not os.path.isfile(log_path):
        return jsonify({"groups": {}})

    per_tube = request.args.get("per_tube", 10, type=int)

    try:
        with open(log_path, "r", encoding="utf-8") as f:
            lines = f.readlines()

        # Collect all entries per tube
        buckets = {}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            tid = entry.get("tube_id", "_system")
            if tid not in buckets:
                buckets[tid] = []
            buckets[tid].append(entry)

        # Keep only last N per tube
        groups = {tid: entries[-per_tube:]
                  for tid, entries in buckets.items()}

        return jsonify({"groups": groups})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@tube_bp.route("/api/tube/log", methods=["DELETE"])
def clear_tube_log():
    """
    Clear tube execution log entries.

    Query params:
        tube_id (str): Only delete entries for this tube (optional).
                       If omitted, clears ALL entries.
    """
    log_path = os.path.join(_tube_dir(), "tube_log.jsonl")
    if not os.path.isfile(log_path):
        return jsonify({"status": "ok", "deleted": 0})

    filter_id = request.args.get("tube_id", None)

    try:
        if filter_id is None:
            # Clear everything
            with open(log_path, "r", encoding="utf-8") as f:
                total = sum(1 for line in f if line.strip())
            with open(log_path, "w", encoding="utf-8") as f:
                f.write("")
            return jsonify({"status": "ok", "deleted": total})
        else:
            # Keep lines that do NOT match the filter
            with open(log_path, "r", encoding="utf-8") as f:
                lines = f.readlines()

            kept = []
            deleted = 0
            for line in lines:
                stripped = line.strip()
                if not stripped:
                    continue
                entry = json.loads(stripped)
                if entry.get("tube_id") == filter_id:
                    deleted += 1
                else:
                    kept.append(line)

            with open(log_path, "w", encoding="utf-8") as f:
                f.writelines(kept)

            return jsonify({"status": "ok", "deleted": deleted,
                            "tube_id": filter_id})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
