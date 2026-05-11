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
| `server/provisioner.py` | Runs stages, calls `transition_status()` + `db.commit()`. Also writes `AuditLog` entries on destroy (line 247) and redeploy (line 309). |
| `server/worker/tasks.py` | ARQ task wrappers. `task_stop_site` and `task_start_site` write `AuditLog` entries directly (lines 71, 85). |
| `server/services/site_service.py` | `transition_status()` — validates + sets `site.status` |
| `web/app/sites/[id]/page.tsx` | Site detail page with 5s `setInterval` polling. Now also shows TTL info, scheduled destruction banner with "Extend TTL" dropdown, share button, and per-day cost estimate. |
| `web/lib/api-client.ts` | `sites.get()` used for polling. Has global 401 → `/login` redirect. |
| `server/main.py` | FastAPI app factory, registers routers (auth, audit_logs, costs, sites, health, deploy_sources, webhooks) |
| `server/config.py` | `Settings` with `redis_url` and granular mock toggles (`use_mock_github`, `use_mock_terraform`, `use_mock_ssm`) |
| `web/next.config.ts` | Rewrites `/api/*` to the FastAPI backend (HTTP only, not WebSocket) |

### What changed since initial plan draft

- **Mock toggles are now granular** — `server/config.py` has `use_mock_github`, `use_mock_terraform`, `use_mock_ssm` properties (not a single `is_local` check). The provisioner's `_get_defaults()` uses these individually.
- **AuditLog entries** are now written in `provisioner.py` (destroy, redeploy) and `worker/tasks.py` (stop, start). The publish calls must go next to these — same spots.
- **Site detail page** is more complex — it now has TTL display, scheduled destruction banner with extend dropdown, share button, and cost estimate. The WebSocket `stageMessage` UI needs to fit alongside these without conflicting.
- **`web/app/layout.tsx`** now includes a `<LogoutButton />` component and nav links to Costs and Audit Log.
- **Invites system** is fully implemented (model, service, pages). Not relevant to WebSocket but good to know it exists.
- **TTL/auto-destroy** is fully wired — `cron_stale_reminders` now sets `scheduled_destroy_at` 12h after TTL expires (not just sends email). Events for scheduled destruction changes could be useful but are out of scope for this plan.

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
import logging
from datetime import datetime, timezone

import redis.asyncio as aioredis

from server.config import get_settings

logger = logging.getLogger(__name__)

async def publish_site_event(site_id: str, event_type: str, status: str | None = None, message: str = "") -> None:
    """Publish a site event to Redis pub/sub. Never raises — failures are logged and swallowed."""
    try:
        settings = get_settings()
        r = aioredis.from_url(settings.redis_url)
        try:
            payload = {
                "type": event_type,
                "status": status,
                "message": message,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            channel = f"flare:site:{site_id}"
            await r.publish(channel, json.dumps(payload))
        finally:
            await r.aclose()
    except Exception:
        logger.warning("Failed to publish event for site %s", site_id, exc_info=True)
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

Modify `server/provisioner.py` — add `await publish_site_event(...)` calls after each `transition_status()` + `db.commit()`.

**Where to add publish calls in `provision_site()` (current line numbers):**

```
Line 170-171: transition_status → PROVISIONING, db.commit()
  → publish: status_change, status="provisioning", message="Resolving deploy source..."

Line 173: sha resolved
  → publish: stage_progress, message=f"Resolved SHA: {sha[:8]}"

Line 176-179: infra.apply, db.commit()
  → publish: stage_progress, message="Infrastructure provisioned"

Line 182-183: transition_status → DEPLOYING, db.commit()
  → publish: status_change, status="deploying", message="Deploying application..."

Line 190: _wait_for_healthy starts
  → publish: stage_progress, message="Waiting for health check..."

Line 195-198: transition_status → RUNNING, db.commit()
  → publish: status_change, status="running", message="Site is live"
```

**In `destroy_site()` (current line numbers):**

```
Line 227-228: transition_status → DESTROYING, db.commit()
  → publish: status_change, status="destroying", message="Destroying site..."

Line 241-245: status = DESTROYED, db.commit()
  → publish: status_change, status="destroyed", message="Site destroyed"
```

**In `redeploy_site()` (current line numbers):**

```
Line 277-280: wake if sleeping
  → publish: stage_progress, message="Waking from sleep..."

Line 286-287: transition_status → DEPLOYING, db.commit()
  → publish: status_change, status="deploying", message="Redeploying..."

Line 306-310: transition_status → RUNNING, db.commit() (+ AuditLog write)
  → publish: status_change, status="running", message="Redeploy complete"

Line 314 (wipe path): wipe triggered
  → publish: stage_progress, message="Health check failed, wiping data and retrying..."

Line 324-328: RUNNING after wipe
  → publish: status_change, status="running", message="Redeploy complete (data wiped)"
```

**Failure cases (all three functions):**
```
→ publish: error, status="failed", message=str(e)[:200]
```

### Step 3: Publish events from worker tasks

`task_stop_site`, `task_start_site`, and `task_sleep_site` in `server/worker/tasks.py` don't go through the provisioner, so they need their own publish calls:

```
task_stop_site:
  Line 65-66: transition_status → STOPPING, db.commit()
    → publish: status_change, status="stopping", message="Stopping site..."
  Line 70-72: status = STOPPED, db.commit()
    → publish: status_change, status="stopped", message="Site stopped"

task_start_site:
  Line 84-86: status = RUNNING, db.commit()
    → publish: status_change, status="running", message="Site started"

task_sleep_site:
  Line 99-100: transition_status → SLEEPING, db.commit()
    → publish: status_change, status="sleeping", message="Site going to sleep"
```

### Step 4: WebSocket endpoint (API process)

Create `server/api/ws.py`:

```python
import json
import logging

import redis.asyncio as aioredis
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from server.config import get_settings

logger = logging.getLogger(__name__)
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

Register in `server/main.py`: add `from server.api import ws` to imports and `app.include_router(ws.router)` after the other routers.

**No auth on WebSocket:** Read-only (server → client), site_id is a UUID, and the page that opens the WebSocket already requires auth.

### Step 5: WebSocket proxy — direct connection to API

The current `next.config.ts` uses `rewrites` which only handles HTTP, not WebSocket. The frontend connects directly to the API port.

In the frontend code:
```typescript
const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
const wsHost = process.env.NEXT_PUBLIC_WS_URL || `${wsProtocol}//${window.location.hostname}:8000`;
const wsUrl = `${wsHost}/api/sites/ws/${siteId}`;
```

Add to `docker-compose.yml` under the `web` service environment:
```yaml
NEXT_PUBLIC_WS_URL: ws://localhost:8000
```

Add to `.env.example`:
```
NEXT_PUBLIC_WS_URL=ws://localhost:8000
```

### Step 6: Frontend WebSocket client

Modify `web/app/sites/[id]/page.tsx`:

- Keep the existing `setInterval` polling as a **fallback**, increase interval from 5s to 15s
- Add a `stageMessage` state for transient progress messages
- Add a WebSocket `useEffect` that:
  - On `status_change`: update `site.status` immediately via `setSite`
  - On `stage_progress`: update `stageMessage`
  - On `error`: update `site.status` to "failed" and set `error_message`
- Clear `stageMessage` 5 seconds after reaching a terminal status

**Important:** The site detail page now has more UI than when the plan was first drafted — TTL display, scheduled destruction banner, extend TTL dropdown, share button, cost estimate. The `stageMessage` should go right below the status badge in the header area (between the `<h1>` + `<StatusBadge>` row and the info grid), not interfere with the existing layout.

```tsx
{stageMessage && (
  <p className="text-sm text-gray-500 animate-pulse">{stageMessage}</p>
)}
```

Place after line 90 (after the `<StatusBadge>` div), before the info grid.

### Step 7: UI for stage progress

```
┌──────────────────────────────────────┐
│  my-site    ● Deploying              │
│  Deploying application...            │  ← stageMessage
│                                      │
│  Domain: my-site.observal.io  Share  │
│  Deploy: branch/main (a1b2c3d4)     │
│  Instance: t3.large                  │
│  Est. Cost: ~$2.00/day              │
│  Time-to-Live: 1 day               │
│  ...                                 │
│                                      │
│  ⚠ Scheduled for destruction...      │  ← existing TTL banner (if applicable)
│                                      │
│  [Redeploy] [Stop] [Destroy] [Logs]  │
└──────────────────────────────────────┘
```

## Dependencies

- `redis[hiredis]` — async Redis client for pub/sub. Check `server/pyproject.toml` — ARQ depends on `redis` already, but verify the version supports async pub/sub (redis >= 4.2). If not present, add `redis>=5.0`.

## Files to create

| File | Purpose |
|------|---------|
| `server/events.py` | `publish_site_event()` function |
| `server/api/ws.py` | WebSocket endpoint |
| `server/tests/test_events.py` | Unit tests for event publishing |
| `server/tests/test_websocket.py` | Integration tests for WebSocket endpoint |

## Files to modify

| File | Change |
|------|--------|
| `server/provisioner.py` | Add `publish_site_event()` calls after each stage transition and AuditLog write |
| `server/worker/tasks.py` | Add publish calls in `task_stop_site`, `task_start_site`, `task_sleep_site` |
| `server/main.py` | Add `from server.api import ws` and `app.include_router(ws.router)` |
| `web/app/sites/[id]/page.tsx` | Add WebSocket client `useEffect`, `stageMessage` state, progress UI below status badge, increase polling interval to 15s |
| `.env.example` | Add `NEXT_PUBLIC_WS_URL=ws://localhost:8000` |
| `docker-compose.yml` | Add `NEXT_PUBLIC_WS_URL: ws://localhost:8000` to web service environment |
| `server/pyproject.toml` | Add `redis>=5.0` if not already present |

## Files NOT modified

| File | Why |
|------|-----|
| `server/services/site_service.py` | `transition_status()` stays pure — publishing happens in the caller |
| `server/api/sites.py` | REST endpoints unchanged. Polling still works |
| `server/api/costs.py` | No cost changes |
| `server/api/audit_logs.py` | No audit log changes |
| `web/lib/api-client.ts` | No new REST endpoints. WebSocket is raw browser API |
| `web/lib/cost-estimate.ts` | No changes |
| `web/app/layout.tsx` | No nav changes needed (includes LogoutButton, Costs, Audit Log links already) |
| `server/database.py` | No schema changes |
| `server/config.py` | No new settings needed (uses existing `redis_url`) |

## Testing strategy

**Unit tests (`server/tests/test_events.py`):**
- `test_publish_site_event` — mock Redis, verify `publish()` called with correct channel and JSON payload
- `test_publish_failure_does_not_raise` — mock Redis to raise, verify no exception propagates
- `test_publish_event_payload_shape` — verify payload includes type, status, message, timestamp

**Integration tests (`server/tests/test_websocket.py`):**
- `test_websocket_receives_event` — connect a WebSocket test client, publish an event to Redis, verify the client receives it
- `test_websocket_disconnect_cleanup` — connect then disconnect, verify pubsub is cleaned up

FastAPI's `TestClient` supports WebSocket testing via `with client.websocket_connect("/api/sites/ws/{id}") as ws`.

**Existing tests must still pass:**
- All 49+ existing tests (provisioner, costs, audit logs, crons, webhooks, site validation, auth roles, invites)
- The publish calls are wrapped in try/except so even if Redis is unavailable during tests, provisioner tests won't fail

**Manual testing:**
- `docker compose up --build`
- Log in, create a site
- Watch the site detail page — stages should appear in real-time as the mocked provisioner runs (~15s total)
- Open browser Network tab → WS tab → verify messages arrive
- Test stop/start/destroy — verify WebSocket delivers those status changes too
- Disconnect WiFi briefly → verify polling fallback keeps the page updated

## Risks and mitigations

| Risk | Mitigation |
|------|-----------|
| Redis pub/sub failure breaks provisioning | All publish calls wrapped in try/except — provisioner never fails due to pub/sub |
| WebSocket connection drops | Polling still runs as fallback (at 15s interval) |
| Memory leak from unclosed pubsub connections | `finally` block in WS endpoint always unsubscribes + closes |
| Stale stage messages shown | Clear `stageMessage` 5s after reaching terminal status |
| Next.js rewrite doesn't proxy WebSocket | Frontend connects directly to API port (no proxy) |
| Publish calls clutter provisioner | Keep them as one-liners — function signature is simple: `publish_site_event(site_id, type, status, message)` |

## Order of implementation

1. `server/events.py` — the publish function (small, testable in isolation)
2. `server/tests/test_events.py` — test it
3. `server/api/ws.py` — WebSocket endpoint
4. `server/main.py` — register router
5. `server/tests/test_websocket.py` — test it
6. `server/provisioner.py` — add publish calls after each stage + in error handlers
7. `server/worker/tasks.py` — add publish calls to stop/start/sleep tasks
8. `web/app/sites/[id]/page.tsx` — WebSocket client + stageMessage UI + slow polling to 15s
9. Config files (`.env.example`, `docker-compose.yml`, `pyproject.toml`)
10. Run full test suite, manual test in Docker
