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


PAYTM_REPORT = """Transaction ID,Order ID,Transaction Date,Updated Date,Transaction Type,Status,Amount,Commission,GST,Settled Amount,Settled Date,UTR No.,Split Flag,Payment Mode
T1,O1,2026-08-20 10:00:00,2026-08-20 10:00:00,ACQUIRING,SUCCESS,"3,000.00",12.00,2.16,2985.84,2026-08-21,UTR001,N,UPI
T2,O2,2026-08-20 11:00:00,2026-08-20 11:00:00,ACQUIRING,SUCCESS,500.00,0.00,0.00,500.00,2026-08-21,UTR001,N,UPI
T3,O3,2026-08-20 12:00:00,2026-08-20 12:00:00,ACQUIRING,SUCCESS,1000.00,18.00,3.24,978.76,2026-08-21,UTR001,N,CC
T4,O4,2026-08-20 13:00:00,,ACQUIRING,PENDING,700.00,,,,,,N,UPI
T5,O5,2026-08-20 14:00:00,,RENTAL,SUCCESS,199.00,0,0,-199.00,2026-08-21,UTR001,N,
R1,O2,2026-08-21 10:00:00,2026-08-21 10:00:00,REFUND,SUCCESS,500.00,0,0,-500.00,2026-08-22,UTR002,N,UPI
R1,O2,2026-08-21 10:00:00,2026-08-21 10:00:00,REFUND,SUCCESS,500.00,0,0,-500.00,2026-08-23,UTR003,N,UPI
"""


def test_a_paytm_settlement_report_is_imported_and_audited(tmp_path):
    from mfp.ingest.paytm_report import MerchantProfile, ReportError, import_report
    from mfp.runtime.system import Runtime

    summary = import_report(PAYTM_REPORT, MerchantProfile(card_credit_rate_percent="1.80"), tmp_path / "up")
    assert (summary.payments, summary.refunds, summary.payouts) == (3, 1, 1)
    assert summary.set_aside == {"other deduction type (RENTAL)": 1, "pending (not final yet)": 1}
    rt = Runtime(tmp_path / "up", start=f"{summary.last_date}T18:00:00")
    rt.connect("UPL-0001")
    rt.run_until_quiet(30)
    found = {c.pattern for c in rt.cases.all("UPL-0001") if c.proof and c.proof.authorises_claim}
    # 0.4% UPI MDR before 15 Oct 2026; GST on a Rs 1,000 card payment (exempt up to Rs 2,000); one refund
    # debited in two payouts. The card MDR itself (1.80%, as agreed) is correct and is not flagged.
    assert found == {"mdr_on_protected_instrument", "gst_on_exempt_settlement", "refund_debited_twice"}
    with pytest.raises(ReportError):
        import_report("Order ID,Amount\n1,2\n", MerchantProfile(), tmp_path / "bad")


def test_the_merchant_master_fills_in_the_merchant_so_nobody_types_rates(loop_datasets, tmp_path):
    import csv
    import io

    from mfp.data.store import MerchantIndex, ObservedDataset
    from mfp.ingest.merchant_master import MerchantMaster
    from mfp.ingest.paytm_report import MerchantProfile, import_report, report_identity

    root = loop_datasets[0]
    view = MerchantIndex(ObservedDataset(root)).view("MER-0001")
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["Transaction ID", "Order ID", "Transaction Date", "Transaction Type", "Status", "Amount", "Commission",
                "GST", "Settled Amount", "Settled Date", "UTR No.", "Payment Mode"])
    for t in [t for t in view.transactions if str(t.kind) == "PAYMENT"][:30]:
        w.writerow([t.txn_id, t.order_ref, t.captured_at.strftime("%Y-%m-%d %H:%M:%S"), "ACQUIRING", "SUCCESS",
                    f"{t.amount_paise / 100:.2f}", "", "", "", "", "", "UPI"])
    report = out.getvalue()

    master = MerchantMaster([root])
    record = master.find(*report_identity(report))
    assert record is not None and record.merchant.merchant_id == "MER-0001" and "transaction IDs" in record.matched_by
    assert master.find("MER-0001", []).matched_by == "merchant ID"
    assert master.find(None, ["NOT-A-REAL-ID"] * 10) is None

    summary = import_report(report, MerchantProfile(legal_name="ignored"), tmp_path / "m", master=record)
    assert summary.merchant_source == "merchant master" and summary.merchant_id == "MER-0001"
    written = ObservedDataset(tmp_path / "m")
    assert written.merchants[0].legal_name == view.merchant.legal_name
    assert len(written.agreements) == len(view.agreements), "the whole agreement history, not one typed rate"

    guessed = import_report(report, MerchantProfile(), tmp_path / "g")
    assert guessed.merchant_source == "form" and guessed.turnover_estimated
