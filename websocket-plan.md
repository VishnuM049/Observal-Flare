# WebSocket Real-Time Updates — Implementation Plan

## Goal

Replace the 5-second polling on the site detail page with a WebSocket connection that pushes provisioning stage updates in real-time. When a user watches a site provision, they see each stage tick by immediately instead of discovering status changes on the next poll.

## Current State

### How status updates work today

1. User creates a site → `POST /api/sites` enqueues an ARQ job (`provision_site`)
2. The ARQ worker picks up the job and calls `provision_site()` in `server/provisioner.py`
3. `provision_site()` calls `transition_status()` + `db.commit()` at each stage (provisioning → deploying → running)
4. The frontend (`web/app/sites/[id]/page.tsx` line 29) polls `GET /api/sites/{id}` every 5 seconds
5. When the poll response has a new status, React re-renders

### Key files

| File | Role |
|------|------|
| `server/provisioner.py` | Runs stages, calls `transition_status()` + `db.commit()` |
| `server/worker/tasks.py` | ARQ task wrappers that call provisioner functions |
| `server/services/site_service.py` | `transition_status()` — validates + sets `site.status` |
| `web/app/sites/[id]/page.tsx` | Site detail page with 5s `setInterval` polling |
| `web/lib/api-client.ts` | `sites.get()` used for polling |
| `server/main.py` | FastAPI app factory, registers routers |
| `server/config.py` | `Settings` with `redis_url` |
| `web/next.config.ts` | Rewrites `/api/*` to the FastAPI backend |

### Infrastructure already available

- **Redis** is running (used by ARQ for task queue) — we can reuse it for pub/sub
- **FastAPI** supports WebSocket endpoints natively
- **ARQ worker** is a separate process from the API — they share the same Redis

## Architecture

```
┌─────────────────┐          ┌──────────────────┐          ┌──────────────┐
│  ARQ Worker      │          │  FastAPI API      │          │  Browser     │
│                  │  Redis   │                   │   WS     │              │
│  provisioner.py  │─pub───►  │  /api/sites/ws/   │──push──► │  site detail │
│  (stage changes) │  pubsub  │  {site_id}        │          │  page.tsx    │
└─────────────────┘          └──────────────────┘          └──────────────┘
```

**Why Redis pub/sub?** The worker and API are separate processes. The worker needs to notify the API process that a status changed. Redis pub/sub is the simplest bridge — we already have Redis, no new infrastructure.

**Channel naming:** `flare:site:{site_id}` — one channel per site. Only active WebSocket connections subscribe; channels with no subscribers cost nothing.

## Implementation Steps

### Step 1: Event publisher (server-side, in worker process)

Create `server/events.py`:

```python
import json
import redis.asyncio as aioredis
from server.config import get_settings

async def publish_site_event(site_id: str, event: dict) -> None:
    """Publish a site status event to Redis pub/sub."""
    settings = get_settings()
    r = aioredis.from_url(settings.redis_url)
    try:
        channel = f"flare:site:{site_id}"
        await r.publish(channel, json.dumps(event))
    finally:
        await r.aclose()
```

Event payload shape:
```json
{
  "type": "status_change",
  "status": "deploying",
  "message": "Deploying application...",
  "timestamp": "2026-05-11T14:30:00Z"
}
```

Possible event types:
- `status_change` — site.status changed (the main one)
- `stage_progress` — informational message within a stage (e.g., "Waiting for health check...")
- `error` — something failed

### Step 2: Publish events from the provisioner

Modify `server/provisioner.py` — add `await publish_site_event(...)` calls after each `transition_status()` + `db.commit()`. This is the critical integration point.

**Where to add publish calls in `provision_site()`:**

```
Line 168-169: transition_status → PROVISIONING, db.commit()
  → publish: {"type": "status_change", "status": "provisioning", "message": "Resolving deploy source..."}

Line 171: sha resolved
  → publish: {"type": "stage_progress", "message": "Resolved SHA: abc123"}

Line 174-177: infra.apply, db.commit()
  → publish: {"type": "stage_progress", "message": "Infrastructure provisioned"}

Line 180-181: transition_status → DEPLOYING, db.commit()
  → publish: {"type": "status_change", "status": "deploying", "message": "Deploying application..."}

Line 188: _wait_for_healthy starts
  → publish: {"type": "stage_progress", "message": "Waiting for health check..."}

Line 193-196: transition_status → RUNNING, db.commit()
  → publish: {"type": "status_change", "status": "running", "message": "Site is live"}
```

Same pattern for `destroy_site()` and `redeploy_site()`.

**Failure case (line 202-208):**
```
→ publish: {"type": "error", "status": "failed", "message": "Provisioning failed: <error>"}
```

**Important:** Publishing must not break provisioning. Wrap every publish call in try/except — if Redis pub/sub fails, log a warning and continue. The provisioner's job is to provision, not to notify browsers.

### Step 3: WebSocket endpoint (API process)

Add to `server/api/ws.py`:

```python
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
import redis.asyncio as aioredis
import json

router = APIRouter()

@router.websocket("/api/sites/ws/{site_id}")
async def site_ws(websocket: WebSocket, site_id: str):
    await websocket.accept()
    
    settings = get_settings()
    r = aioredis.from_url(settings.redis_url)
    pubsub = r.pubsub()
    channel = f"flare:site:{site_id}"
    await pubsub.subscribe(channel)
    
    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                await websocket.send_text(message["data"].decode())
    except WebSocketDisconnect:
        pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.aclose()
        await r.aclose()
```

Register in `server/main.py`: `app.include_router(ws.router)`

**No auth on WebSocket:** The WebSocket endpoint doesn't need session cookie validation. It's read-only (server → client) and the site_id is a UUID that's not guessable. The site detail page already requires auth to load — the WebSocket just streams updates for a site you can already see.

### Step 4: WebSocket proxy in Next.js

The current `next.config.ts` uses `rewrites` which only handles HTTP, not WebSocket. Two options:

**Option A (recommended): Direct WebSocket to API port**

The frontend connects to `ws://localhost:8000/api/sites/ws/{id}` directly (in dev) or `wss://flare.observal.io:8000/...` (in prod, if API is exposed). No proxy changes needed.

In the frontend code, determine the WebSocket URL from the current page URL:
```typescript
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const wsHost = process.env.NEXT_PUBLIC_WS_URL || `${wsProtocol}//${window.location.hostname}:8000`;
const wsUrl = `${wsHost}/api/sites/ws/${siteId}`;
```

Add `NEXT_PUBLIC_WS_URL` to `.env.example` and `docker-compose.yml`.

**Option B: Add WebSocket support to Next.js proxy**

Use a custom server or middleware to proxy WebSocket. More complex, fragile, and unnecessary for an internal tool.

### Step 5: Frontend WebSocket client

Modify `web/app/sites/[id]/page.tsx`:

- Keep the existing `setInterval` polling as a **fallback** (in case WebSocket disconnects)
- Add a WebSocket connection that listens for events
- When a `status_change` event arrives, update the site state immediately
- When a `stage_progress` event arrives, show it as a transient message
- Auto-reconnect on disconnect with exponential backoff

```typescript
// Inside SiteDetailPage component

const [stageMessage, setStageMessage] = useState<string | null>(null);

useEffect(() => {
  const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
  const wsHost = process.env.NEXT_PUBLIC_WS_URL || `${wsProtocol}//${window.location.hostname}:8000`;
  const ws = new WebSocket(`${wsHost}/api/sites/ws/${id}`);
  
  ws.onmessage = (e) => {
    const event = JSON.parse(e.data);
    if (event.type === "status_change") {
      setSite(prev => prev ? { ...prev, status: event.status } : prev);
      setStageMessage(event.message);
    } else if (event.type === "stage_progress") {
      setStageMessage(event.message);
    } else if (event.type === "error") {
      setSite(prev => prev ? { ...prev, status: "failed", error_message: event.message } : prev);
    }
  };
  
  ws.onclose = () => {
    // Polling is still running as fallback — no reconnect needed for MVP
  };
  
  return () => ws.close();
}, [id]);
```

**Keep the 5s polling.** It serves as fallback if the WebSocket disconnects and as the source of truth for full site data (the WebSocket only sends status + messages, not the entire site object). Increase polling interval to 15s or 30s since WebSocket handles the real-time updates.

### Step 6: UI for stage progress

Add a small progress indicator below the status badge on the site detail page:

```
┌──────────────────────────────────────┐
│  my-site    ● Deploying              │
│             Deploying application... │  ← stageMessage, shown during active operations
│                                      │
│  Domain: my-site.observal.io         │
│  ...                                 │
└──────────────────────────────────────┘
```

The `stageMessage` fades out 5 seconds after the site reaches a terminal status (running, failed, destroyed).

## Dependencies

- `redis[hiredis]` — async Redis client for pub/sub. Check if already installed (ARQ may bundle it). If not: add `redis>=5.0` to `server/pyproject.toml`.

## Files to create

| File | Purpose |
|------|---------|
| `server/events.py` | `publish_site_event()` function |
| `server/api/ws.py` | WebSocket endpoint |

## Files to modify

| File | Change |
|------|--------|
| `server/provisioner.py` | Add `publish_site_event()` calls after each stage transition |
| `server/worker/tasks.py` | Add publish calls in task wrappers for stop/start/sleep (they don't go through provisioner) |
| `server/main.py` | Register `ws.router` |
| `web/app/sites/[id]/page.tsx` | Add WebSocket client, show stage messages, slow down polling |
| `.env.example` | Add `NEXT_PUBLIC_WS_URL` |
| `docker-compose.yml` | Pass `NEXT_PUBLIC_WS_URL` to web service |
| `server/pyproject.toml` | Add `redis>=5.0` if not already present |

## Files NOT modified

| File | Why |
|------|-----|
| `server/services/site_service.py` | `transition_status()` stays pure — it just sets status on the model. Publishing happens in the caller (provisioner). |
| `server/api/sites.py` | REST endpoints unchanged. Polling still works. |
| `web/lib/api-client.ts` | No new REST endpoints needed. |
| `server/database.py` | No schema changes. |

## Testing strategy

**Unit tests (`server/tests/test_events.py`):**
- `test_publish_site_event` — mock Redis, verify `publish()` called with correct channel and JSON payload
- `test_publish_failure_does_not_raise` — mock Redis to raise, verify no exception propagates

**Integration tests (`server/tests/test_websocket.py`):**
- `test_websocket_receives_event` — connect a WebSocket test client, publish an event to Redis, verify the client receives it
- `test_websocket_disconnect_cleanup` — connect then disconnect, verify pubsub is cleaned up

FastAPI's `TestClient` supports WebSocket testing via `with client.websocket_connect("/api/sites/ws/{id}") as ws`.

**Manual testing:**
- `docker compose up --build`
- Log in, create a site
- Watch the site detail page — stages should appear in real-time as the mocked provisioner runs (~15s total)
- Open browser network tab → WS tab → verify messages arrive

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Redis pub/sub failure breaks provisioning | All publish calls wrapped in try/except — provisioner never fails due to pub/sub |
| WebSocket connection drops | Polling still runs as fallback (at 15s interval) |
| Memory leak from unclosed pubsub connections | `finally` block in WS endpoint always unsubscribes + closes |
| Stale stage messages shown | Clear `stageMessage` 5s after reaching terminal status |
| Next.js rewrite doesn't proxy WebSocket | Frontend connects directly to API port (no proxy) |

## Order of implementation

1. `server/events.py` — the publish function (small, testable in isolation)
2. `server/tests/test_events.py` — test it
3. `server/api/ws.py` — WebSocket endpoint
4. `server/main.py` — register router
5. `server/tests/test_websocket.py` — test it
6. `server/provisioner.py` — add publish calls
7. `server/worker/tasks.py` — add publish calls to stop/start/sleep tasks
8. `web/app/sites/[id]/page.tsx` — WebSocket client + stage message UI
9. Config files (`.env.example`, `docker-compose.yml`, `pyproject.toml`)
10. Run full test suite, manual test in Docker
