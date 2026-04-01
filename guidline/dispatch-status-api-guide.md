# Dispatch Status API — Developer Guide

## What It Does

Each dispatch strategy writes a status file (`staging/active_session.json`) while running. The file exists only during execution and is deleted on completion. This provides a simple mechanism for any module to check whether a dispatch is currently active.

## API Endpoint

```
GET /api/dispatch/<strategy_name>/status
```

### Response — Idle

```json
{"status": "idle"}
```

### Response — Running

```json
{
  "session_id": "20260331-134858-dd6eb2b0",
  "status": "running",
  "current_round": 2,
  "current_agent_id": "agent2",
  "current_agent_name": "Critic",
  "updated_at": "2026-03-31T13:49:12+0000"
}
```

## How It Works Internally

1. `dispatch.py` calls `_set_status()` when `run_streaming()` starts
2. Before each agent call, `_set_status()` updates `current_round`, `current_agent_id`, `current_agent_name`
3. When all rounds complete, `_clear_status()` deletes the file
4. `get_status()` reads the file if it exists, returns `{"status": "idle"}` if not

Status file location: `dispatch/<strategy>/staging/active_session.json`

## Usage Examples

### Frontend — Show Agent Thinking Status

```javascript
async function pollStatus() {
    const resp = await fetch('/api/dispatch/roundtable/status');
    const data = await resp.json();
    if (data.status === 'running') {
        statusBar.textContent = `${data.current_agent_name} is thinking... (Round ${data.current_round})`;
    } else {
        statusBar.textContent = 'Ready';
    }
}
setInterval(pollStatus, 2000);
```

### Index Page — Status Indicator on Card

```javascript
const resp = await fetch('/api/dispatch/roundtable/status');
const data = await resp.json();
const dot = data.status === 'running' ? '🟢 Running' : '⚪ Idle';
card.querySelector('.status').textContent = dot;
```

### Scheduler — Check Before Triggering

```python
import requests

status = requests.get('http://127.0.0.1:5000/api/dispatch/roundtable/status').json()
if status.get('status') == 'running':
    print(f"Busy: {status['current_agent_name']} Round {status['current_round']}, skipping")
else:
    # Safe to trigger
    requests.post('http://127.0.0.1:5000/api/dispatch/roundtable/sessions', ...)
```

### Another Dispatch Strategy — Check Agent Availability

```python
import requests

status = requests.get('http://127.0.0.1:5000/api/dispatch/roundtable/status').json()
if status.get('status') == 'running' and status.get('current_agent_id') == 'default':
    # Agent "default" is busy in roundtable, wait or use a different agent
    pass
```

## Files Involved

| File | Role |
|------|------|
| `dispatch/<strategy>/dispatch.py` | Writes/clears `_set_status()`, exposes `get_status()` |
| `dispatch/<strategy>/staging/active_session.json` | Status file (exists only while running) |
| `dispatch_routes.py` | `GET /api/dispatch/<strategy>/status` route |

## Edge Cases

- **Server crash during run**: Status file may remain. On next startup, check file age and clear stale status files.
- **Concurrent requests**: Current design is single-writer. If two users send messages simultaneously, the second call will overwrite the status. This is acceptable for single-user local deployment. For multi-user, add a lock or reject if status is already `running`.
