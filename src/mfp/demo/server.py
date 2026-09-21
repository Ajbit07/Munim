"""Local HTTP API for the command center, the demo, and the n8n workflow.

Everything is served from this process: the UI is static files in ui/, and
there are no external requests. One demo director owns one runtime; a lock
serialises anything that mutates it.
"""

from __future__ import annotations

import threading
from collections import deque
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from mfp.core.enums import Actor
from mfp.demo.director import DemoDirector
from mfp.runtime.views import case_detail, human_queue, late_settlements

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
        "redteam": d.redteam, "baseline": d.baseline, "notification": d.notification,
        "dataset": {"seed": d.seed, "data_through": rt.data_through.isoformat(),
                    "merchants": len(rt.index.merchant_ids("FULL")), "connected": len(rt.connected)},
        "events": len(rt.events),
    }


@app.get("/")
def index() -> FileResponse:
    return FileResponse(UI / "index.html")


@app.get("/chat")
def chat_page() -> FileResponse:
    return FileResponse(UI / "chat.html")


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
        seed = _state["d"].seed if "d" in _state else 42
        _state["d"] = DemoDirector(seed=seed)
        _assistants.clear()
        return _state_payload(_state["d"])


# -- live operation ------------------------------------------------------------------------------


@app.get("/api/merchants")
def merchants() -> list[dict[str, Any]]:
    with _lock:
        return director().merchants()


@app.post("/api/live/select")
def live_select(body: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        d = director()
        try:
            d.select(str(body.get("merchant_id", "")))
        except KeyError:
            raise HTTPException(404, "No such merchant in this dataset") from None
        _assistants.clear()
        return _state_payload(d)


@app.post("/api/redteam/run")
def redteam_run() -> dict[str, Any]:
    with _lock:
        d = director()
        d.run_redteam()
        return _state_payload(d)


@app.post("/api/baseline/run")
def baseline_run() -> dict[str, Any]:
    with _lock:
        d = director()
        if not (d.data_dir / f"seed-{d.seed}-baseline" / "manifest.json").exists():
            raise HTTPException(409, "This dataset has no clean baseline")
        d.run_baseline()
        return _state_payload(d)


@app.post("/api/notify")
def notify() -> dict[str, Any]:
    with _lock:
        d = director()
        if d.merchant_id not in d.rt.connected:
            raise HTTPException(409, "Connect the merchant first")
        d.compose_notification()
        return _state_payload(d)


# -- fresh datasets: generated on request, so nothing on screen can be pre-built ------------------

_generation: dict[str, Any] = {"state": "idle"}


def _available_seeds() -> list[int]:
    root = REPO / "data" / "generated"
    seeds = []
    for p in root.glob("seed-*"):
        tail = p.name[len("seed-"):]
        if tail.isdigit() and (p / "manifest.json").exists() and (root / f"{p.name}-baseline" / "manifest.json").exists():
            seeds.append(int(tail))
    return sorted(seeds)


def _generate(seed: int) -> None:
    import subprocess
    import sys
    import time

    started = time.monotonic()
    try:
        for extra in ([], ["--no-leakage"]):
            done = subprocess.run([sys.executable, str(REPO / "generate.py"), "--seed", str(seed), *extra],
                                  capture_output=True, text=True, cwd=REPO)
            if done.returncode != 0:
                raise RuntimeError(done.stderr.strip()[-300:] or "generator failed")
        _generation.update(state="ready", seconds=round(time.monotonic() - started))
    except Exception as exc:  # reported to the page, never raised into the server
        _generation.update(state="failed", error=str(exc))


@app.get("/api/datasets")
def datasets() -> dict[str, Any]:
    with _lock:
        current = director().seed
    return {"current": current, "available": _available_seeds(), "generation": dict(_generation)}


@app.post("/api/datasets/new")
def datasets_new() -> dict[str, Any]:
    import os

    if _generation.get("state") == "generating":
        raise HTTPException(409, "A dataset is already being generated")
    seed = 1000 + int.from_bytes(os.urandom(2), "big") % 90000  # a seed nobody chose in advance
    _generation.clear()
    _generation.update(state="generating", seed=seed)
    threading.Thread(target=_generate, args=(seed,), daemon=True).start()
    return dict(_generation)


@app.post("/api/datasets/use")
def datasets_use(body: dict[str, Any]) -> dict[str, Any]:
    seed = int(body.get("seed", -1))
    if seed not in _available_seeds():
        raise HTTPException(404, f"No generated dataset for seed {seed}")
    with _lock:
        _state["d"] = DemoDirector(seed=seed)
        _assistants.clear()
        return _state_payload(_state["d"])


@app.post("/api/clock/advance")
def advance(days: int = 1) -> dict[str, Any]:
    with _lock:
        d = director()
        if d.merchant_id not in d.rt.connected:
            raise HTTPException(409, "Connect a merchant first.")
        d.rt.advance(max(1, min(days, 30)))
        return _state_payload(d)


@app.get("/api/cases")
def cases() -> list[dict[str, Any]]:
    with _lock:
        d = director()
        return [c.summary() for c in sorted(d.rt.cases.all(d.merchant_id), key=lambda c: (c.month, c.case_id),
                                            reverse=True)]


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
        d = director()
        return [c for c in human_queue(d.rt) if c["merchant_id"] == d.merchant_id]


@app.post("/api/cases/{case_id}/review")
def review(case_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        d = director()
        try:
            d.rt.review(case_id, body.get("action", ""), body.get("reviewer") or "Merchant success desk",
                        (body.get("note") or "").strip())
        except KeyError:
            raise HTTPException(404, f"No case {case_id}") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None
        return {"case": case_detail(d.rt, case_id)["case"], "state": _state_payload(d)}


@app.get("/api/late-settlements")
def delays() -> dict[str, Any]:
    with _lock:
        d = director()
        return late_settlements(d.rt, d.merchant_id)


@app.get("/api/root-causes")
def root_causes() -> list[dict[str, Any]]:
    with _lock:
        d = director()
        return [rc.summary() for rc in d.rt.root_causes(d.merchant_id)]


@app.get("/api/network")
def network() -> dict[str, Any]:
    with _lock:
        rt = director().rt
        patterns, suppressed = rt.network.patterns()
        return {"signatures": len(rt.network), "k": rt.network.k, "suppressed_below_k": suppressed,
                "patterns": [p.summary() for p in patterns]}


# -- n8n calls back here for each lifecycle step it executes -------------------------------
# Deliberately lock-free: the runtime holds its lock while it waits for n8n, and n8n
# waits for this callback. Taking the lock here would deadlock the two. The engine
# records the step on the event spine itself, from n8n's reply.

_n8n_receipts: deque[dict[str, Any]] = deque(maxlen=2000)


@app.post("/api/workflow/step")
def workflow_step(body: dict[str, Any]) -> dict[str, Any]:
    _n8n_receipts.append({k: body.get(k) for k in ("action", "claim_id", "case_id", "merchant_id")})
    return {"ok": True, "received": body.get("action")}


@app.get("/api/workflow/receipts")
def workflow_receipts() -> dict[str, Any]:
    return {"count": len(_n8n_receipts), "recent": list(_n8n_receipts)[-20:]}


# -- merchant chat (Sarvam, else the local model, else the checked template) ---------------------

_assistants: dict[int, Any] = {}


def assistant():
    from mfp.assistant.chat import MerchantAssistant

    d = director()
    key = id(d.rt)
    if key not in _assistants:
        _assistants.clear()
        _assistants[key] = MerchantAssistant(d.rt, d.merchant_id, lock=_lock)
    return _assistants[key]


@app.on_event("startup")
def _warm_local_model() -> None:
    # Load the local model in the background so the first merchant question is not a minute-long wait.
    from mfp.assistant.backends import OllamaChat

    threading.Thread(target=OllamaChat().warm, daemon=True).start()


@app.get("/api/chat/status")
def chat_status() -> dict[str, Any]:
    a = assistant()
    with _lock:
        return a.status()


@app.post("/api/chat")
def chat(body: dict[str, Any]) -> dict[str, Any]:
    message = str(body.get("message", ""))
    if not message.strip():
        raise HTTPException(400, "message is empty")
    history = body.get("history") if isinstance(body.get("history"), list) else []
    result = assistant().reply(message, str(body.get("language", "auto")), history)
    with _lock:
        director().rt.events.append(Actor.SYSTEM, "merchant.chat", merchant_id=director().merchant_id,
                                    topics=result["topics"], source=result["source"], guarded=bool(result["note"]))
    return result


@app.post("/api/chat/proactive")
def chat_proactive(body: dict[str, Any]) -> dict[str, Any]:
    d = director()
    if d.merchant_id not in d.rt.connected:
        raise HTTPException(409, "the audit has not run yet")
    return assistant().proactive(str(body.get("language", "hinglish")))


@app.post("/api/chat/decide")
def chat_decide(body: dict[str, Any]) -> dict[str, Any]:
    action = str(body.get("action", ""))
    if action not in ("file", "dismiss"):
        raise HTTPException(400, "action must be file or dismiss")
    try:
        return assistant().decide(str(body.get("case_id", "")), action, str(body.get("language", "hinglish")))
    except KeyError:
        raise HTTPException(404, "No such case for this merchant") from None
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from None


@app.get("/api/tickets")
def tickets() -> list[dict[str, Any]]:
    with _lock:
        d = director()
        return [t.summary() for t in d.rt.tickets.for_merchant(d.merchant_id)]


@app.post("/api/tickets/{ticket_id}/resolve")
def ticket_resolve(ticket_id: str, body: dict[str, Any]) -> dict[str, Any]:
    with _lock:
        d = director()
        try:
            return d.rt.tickets.resolve(ticket_id, body.get("resolved_by") or "Merchant success desk",
                                        body.get("resolution") or "Checked with the merchant").summary()
        except KeyError:
            raise HTTPException(404, f"No ticket {ticket_id}") from None
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None


@app.post("/api/chat/listen")
async def chat_listen(request: Request):
    from fastapi.responses import Response

    audio = await request.body()
    if len(audio) > 5_000_000:
        raise HTTPException(413, "voice note too long")
    heard = assistant().transcribe(audio, request.headers.get("content-type", "audio/webm"))
    if heard is None:
        return Response(status_code=204)  # no Sarvam key: the page falls back to the browser's recogniser
    return heard


@app.get("/api/impact")
def impact() -> dict[str, Any]:
    """Before and after, from computed figures only."""
    with _lock:
        d = director()
        rt, mid = d.rt, d.merchant_id
        if mid not in rt.connected:
            raise HTTPException(409, "the audit has not run yet")
        m = rt.metrics(mid)
        view = rt.view(mid)
        through = rt.observed_through()
        late = late_settlements(rt, mid)
        return {
            "merchant": rt.dataset.merchant(mid).legal_name,
            "lines_checked": sum(1 for line in view.settlement_lines
                                 if view.batches_by_id[line.batch_id].settlement_date <= through),
            "batches_checked": sum(1 for b in view.settlement_batches if b.settlement_date <= through),
            "months": m["months_affected"],
            "identified_paise": m["identified_paise"], "recovered_paise": m["recovered_paise"],
            "in_progress_paise": m["in_progress_paise"], "escalated": m["escalated"],
            "escalated_paise": m["escalated_paise"], "cases": m["proven_cases"] + m["escalated"],
            "future_leakage_prevented_paise": m["future_leakage_prevented_paise"],
            "projected_leakage_paise": m["projected_leakage_paise"],
            "late_payments": late["payments"], "late_paise": late["held_up_paise"],
            "complaints_raised": m["open_complaints"],
            "false_claims": d.redteam["false_claims"] if d.redteam else None,
        }


@app.post("/api/chat/speak")
def chat_speak(body: dict[str, Any]):
    from fastapi.responses import Response

    audio = assistant().speak(str(body.get("text", ""))[:1500], str(body.get("language", "hinglish")))
    if audio is None:
        return Response(status_code=204)  # no Sarvam key: the page uses the browser's own voice
    return Response(content=audio, media_type="audio/wav")
