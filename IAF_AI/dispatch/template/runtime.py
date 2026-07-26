"""
Runtime — Thread-local session context and SSE event queue.

Used by Chat Dispatch to let tool handlers (e.g. search_tools.web_search)
know the current session_id and emit agent_activity events back to the
streaming SSE connection.

Design notes:
  - threading.local works for Flask's default synchronous threading model.
  - If we migrate to async / gevent in the future, switch to
    contextvars.ContextVar here (API stays the same for callers).
"""

import queue
import threading

_current = threading.local()


# --- Session context -------------------------------------------------------

def set_current_sid(sid):
    """Bind the current session_id to this thread."""
    _current.sid = sid


def get_current_sid():
    """Return the session_id bound to this thread, or None."""
    return getattr(_current, "sid", None)


def clear_current_sid():
    """Unbind the session_id from this thread."""
    _current.sid = None


# --- SSE event queue -------------------------------------------------------

def _ensure_queue():
    if not hasattr(_current, "events"):
        _current.events = queue.Queue()


def emit_event(event_dict):
    """Queue an SSE event dict. dispatch.py will drain it after tool loop."""
    _ensure_queue()
    _current.events.put(event_dict)


def drain_events():
    """Pop all pending events. Returns list of event dicts (possibly empty)."""
    _ensure_queue()
    events = []
    while not _current.events.empty():
        try:
            events.append(_current.events.get_nowait())
        except queue.Empty:
            break
    return events


def clear_events():
    """Discard any queued events. Call between requests as safety."""
    if hasattr(_current, "events"):
        _current.events = queue.Queue()
