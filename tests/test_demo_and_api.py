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
