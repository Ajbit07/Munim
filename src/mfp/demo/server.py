"""Local HTTP API for the command center, the demo, and the n8n workflow.

Everything is served from this process: the UI is static files in ui/, and
there are no external requests. One demo director owns one runtime; a lock
serialises anything that mutates it.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mfp.core.enums import Actor
from mfp.demo.director import DemoDirector
from mfp.runtime.views import case_detail, human_queue

REPO = Path(__file__).resolve().parents[3]
UI = REPO / "ui"

app = FastAPI(title="Merchant Financial Protection Agent", docs_url="/api/docs")


@app.middleware("http")
async def no_stale_ui(request, call_next):
    # The UI changes between rehearsals; never let a browser show an old copy on stage.
    response = await call_next(request)
    if request.url.path == "/" or request.url.path.startswith("/ui/"):
        response.headers["Cache-Control"] = "no-store"
    return response
_lock = threading.RLock()
_state: dict[str, DemoDirector] = {}


def director() -> DemoDirector:
    with _lock:
        if "d" not in _state:
            _state["d"] = DemoDirector()
        return _state["d"]


def _state_payload(d: DemoDirector) -> dict[str, Any]:
    rt = d.rt
    m = rt.dataset.merchant(d.merchant_id)
    return {
        "today": rt.clock.today().isoformat(),
        "merchant": {"merchant_id": m.merchant_id, "legal_name": m.legal_name, "mcc": m.registered_mcc,
                     "acquirer": m.acquirer_id, "city": m.city, "connected": m.merchant_id in rt.connected},
        "metrics": rt.metrics(d.merchant_id),
        "adapters": {"workflow": rt.workflow.name, "memory": rt.memory.name, "reasoner": rt.reasoner.name,
                     "notifier": rt.notifier.name},
        "steps": d.outline(),
        "last_step": d.results[-1] if d.results else None,
        "redteam": d.redteam, "baseline": d.baseline,
        "events": len(rt.events),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI / "index.html")


app.mount("/ui", StaticFiles(directory=UI), name="ui")


@app.get("/api/state")
def state() -> dict[str, Any]:
    with _lock:
        return _state_payload(director())


@app.get("/api/events")
def events(since: int = 0, limit: int = 400) -> dict[str, Any]:
    with _lock:
        log = director().rt.events.events
        chunk = log[since:since + limit]
        return {"next": since + len(chunk), "events": [
            {"seq": e.seq, "ts": e.ts, "actor": str(e.actor), "kind": e.kind, "case_id": e.case_id,
             "merchant_id": e.merchant_id, "payload": e.payload} for e in chunk]}


@app.post("/api/demo/next")
def demo_next() -> dict[str, Any]:
    with _lock:
        d = director()
        step = d.next()
        return {"step": step, "state": _state_payload(d)}


@app.post("/api/demo/reset")
def demo_reset() -> dict[str, Any]:
    with _lock:
        _state.pop("d", None)
        return _state_payload(director())


@app.post("/api/clock/advance")
def advance(days: int = 1) -> dict[str, Any]:
    with _lock:
        d = director()
        if d.merchant_id not in d.rt.connected:
            raise HTTPException(409, "Connect the merchant first: run the demo to the audit step.")
        d.rt.advance(max(1, min(days, 30)))
        return _state_payload(d)


@app.get("/api/cases")
def cases() -> list[dict[str, Any]]:
    with _lock:
        rt = director().rt
        return [c.summary() for c in sorted(rt.cases.all(), key=lambda c: (c.month, c.case_id), reverse=True)]


@app.get("/api/cases/{case_id}")
def case(case_id: str) -> dict[str, Any]:
    with _lock:
        rt = director().rt
        try:
            return case_detail(rt, case_id)
        except KeyError:
            raise HTTPException(404, f"No case {case_id}") from None


@app.get("/api/human-queue")
def queue() -> list[dict[str, Any]]:
    with _lock:
        return human_queue(director().rt)


@app.get("/api/root-causes")
def root_causes() -> list[dict[str, Any]]:
    with _lock:
        return [rc.summary() for rc in director().rt.root_causes()]


@app.get("/api/network")
def network() -> dict[str, Any]:
    with _lock:
        rt = director().rt
        patterns, suppressed = rt.network.patterns()
        return {"signatures": len(rt.network), "k": rt.network.k, "suppressed_below_k": suppressed,
                "patterns": [p.summary() for p in patterns]}


# -- n8n calls back here for each lifecycle step it executes -------------------------------


@app.post("/api/workflow/step")
def workflow_step(body: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        rt = director().rt
        rt.events.append(Actor.WORKFLOW_ENGINE, "workflow.n8n.step", case_id=body.get("case_id"),
                         merchant_id=body.get("merchant_id"), action=body.get("action"),
                         claim_id=body.get("claim_id"), engine="n8n")
        return {"ok": True, "received": body.get("action")}
