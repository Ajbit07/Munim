"""The demo story and the command-center API run end to end on generated data."""

from __future__ import annotations

import pytest

from mfp.demo.director import DemoDirector


@pytest.fixture(scope="module")
def director(loop_datasets):
    return DemoDirector(data_dir=loop_datasets[0].parent, seed=5)


def test_the_twelve_step_story_runs_from_real_data(director):
    results = director.run_all()
    assert [r["n"] for r in results] == list(range(1, 13))
    by_key = {r["key"]: r["data"] for r in results}
    assert by_key["zero_complaints"]["open_complaints"] == 0
    assert by_key["backfill"]["cases_total"] > 0
    assert by_key["proof"]["proof"]["verdict"] == "PROVEN"
    assert by_key["claim"]["claim"]["reference"]
    assert by_key["recovery"]["metrics"]["recovered_paise"] > 0
    assert by_key["red_team"]["false_claims"] == 0
    assert (by_key["baseline"]["proven_cases"], by_key["baseline"]["claims_filed"]) == (0, 0)
    assert "₹" in by_key["notify"]["text"]


def test_api_serves_state_steps_cases_and_ui(loop_datasets):
    from fastapi.testclient import TestClient

    from mfp.demo import server

    server._state["d"] = DemoDirector(data_dir=loop_datasets[0].parent, seed=5)
    client = TestClient(server.app)
    assert "Settlement Teammate" in client.get("/").text
    assert client.get("/api/state").json()["metrics"]["open_complaints"] == 0
    assert client.post("/api/clock/advance").status_code == 409  # nothing connected yet
    for _ in range(3):
        assert client.post("/api/demo/next").status_code == 200
    cases = client.get("/api/cases").json()
    assert cases
    detail = client.get(f"/api/cases/{cases[0]['case_id']}").json()
    assert {"transactions", "lines", "rules", "proof", "history"} <= detail.keys()
    events = client.get("/api/events?since=0").json()
    assert events["next"] > 0 and events["events"][0]["kind"] == "system.started"
    assert client.get("/api/cases/NOPE").status_code == 404
    server._state.clear()


def test_live_mode_runs_any_merchant_on_demand(loop_datasets):
    from fastapi.testclient import TestClient

    from mfp.demo import server

    server._state["d"] = DemoDirector(data_dir=loop_datasets[0].parent, seed=5)
    server._assistants.clear()
    client = TestClient(server.app)
    merchants = client.get("/api/merchants").json()
    assert len(merchants) > 1 and not any(m["connected"] for m in merchants)
    other = merchants[1]["merchant_id"]  # deliberately not the demo's hero
    state = client.post("/api/live/select", json={"merchant_id": other}).json()
    assert state["merchant"]["merchant_id"] == other and state["merchant"]["connected"]
    assert all(c["merchant_id"] == other for c in client.get("/api/cases").json())
    assert client.post("/api/live/select", json={"merchant_id": "NOPE"}).status_code == 404
    before = client.get("/api/state").json()["today"]
    assert client.post("/api/clock/advance?days=2").json()["today"] > before
    assert client.post("/api/redteam/run").json()["redteam"]["false_claims"] == 0
    assert client.post("/api/notify").json()["notification"]["text"]
    assert client.post("/api/datasets/use", json={"seed": 999999}).status_code == 404
    listing = client.get("/api/datasets").json()
    assert listing["generation"]["state"] in ("idle", "ready", "failed", "generating")
    server._state.clear()
    server._assistants.clear()


def test_ops_portfolio_covers_every_merchant(loop_datasets):
    from fastapi.testclient import TestClient

    from mfp.demo import server

    server._state["d"] = DemoDirector(data_dir=loop_datasets[0].parent, seed=5)
    server._assistants.clear()
    client = TestClient(server.app)
    empty = client.get("/api/portfolio").json()
    assert empty["merchants_monitored"] == 0 and empty["merchants_total"] == len(empty["merchants"]) > 1
    focus = client.get("/api/state").json()["merchant"]["merchant_id"]
    client.post("/api/live/connect-all")
    p = client.get("/api/portfolio").json()
    assert p["merchants_monitored"] == p["merchants_total"] and p["focus"] == focus
    assert p["identified_paise"] == sum(m["identified_paise"] for m in p["merchants"]) > 0
    assert p["merchant_complaints"] == 0
    server._state.clear()
    server._assistants.clear()
