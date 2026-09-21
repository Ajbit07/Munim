"""Merchant chat: facts come from the runtime, models only phrase them, guards keep them honest.

No test here touches the network: model backends are replaced with fakes.
"""

from __future__ import annotations

import json
import re

import pytest

from mfp.assistant import backends as backends_module
from mfp.assistant.backends import BackendChain, OllamaChat, SarvamChat, SarvamClient
from mfp.assistant.chat import (
    MerchantAssistant,
    _topics,
    amounts_in,
    detect_language,
    inr,
    reply_problem,
    strip_preamble,
    unsupported_amounts,
)
from mfp.core.enums import CaseState
from mfp.runtime.system import Runtime

HERO = "MER-0001"


class FakeSarvam:
    def __init__(self, reply=None, *, configured=True):
        self.reply, self.calls = reply, []
        self.client = type("C", (), {"configured": configured, "translate": lambda *a, **k: None})()
        self.name = "sarvam:fake"

    def complete(self, system, messages):
        self.calls.append((system, messages))
        return self.reply(messages) if callable(self.reply) else self.reply


class FakeLocal:
    def __init__(self, reply=None, *, available=True, topic=None):
        self.reply, self.topic, self._available, self.calls = reply, topic, available, []
        self.name, self.model = "local:fake", "fake"

    def available(self):
        return self._available

    def complete(self, system, messages, *, schema=None, max_tokens=220):
        self.calls.append(schema is not None)
        if schema is not None:
            return json.dumps({"topic": self.topic or "other"})
        return self.reply(messages) if callable(self.reply) else self.reply


def chain(sarvam=None, local=None) -> BackendChain:
    return BackendChain(sarvam or FakeSarvam(configured=False), local or FakeLocal(available=False))


@pytest.fixture(scope="module")
def rt(loop_datasets):
    root = loop_datasets[0]
    as_of = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["as_of"]
    runtime = Runtime(root, start=f"{as_of}T18:00:00")
    runtime.connect(HERO)
    runtime.run_until_quiet(60)
    return runtime


# -- routing and language -----------------------------------------------------------------------


def test_questions_route_to_the_right_records():
    assert _topics("Soundbox rental kyun kata?") == ["rental"]
    assert _topics("Settlement late kyun aaya?") == ["late"]
    assert _topics("GST zyada kyun laga") == ["tax"]
    assert _topics("what is the current status") == ["summary"]  # "current" is not "rent"
    assert _topics("hello") == ["greet"]
    assert _topics("thanks bhai") == ["greet"]


def test_language_is_read_from_the_script_the_merchant_types_in():
    assert detect_language("मेरा पैसा कब आएगा?") == "hi-IN"
    assert detect_language("என் பணம் எப்போது வரும்?") == "ta-IN"
    assert detect_language("mera paisa kab aayega") == "hinglish"
    assert detect_language("when will my money come") == "en-IN"


# -- the guards ---------------------------------------------------------------------------------


def test_amount_guard_reads_every_way_of_writing_money():
    assert amounts_in("₹1,744.78, Rs 500, 999₹, १२३ रुपये") == {amounts_in("₹1744.78").pop(),
                                                               *amounts_in("₹500 ₹999 ₹123")}
    assert unsupported_amounts("₹1,744.78 and ₹1,745", {174478}) == set()  # exact or rounded is fine
    assert unsupported_amounts("₹1,744.78 and ₹9,999", {174478}) == {amounts_in("₹9999").pop()}


def test_quality_guard_rejects_loops_wrong_language_and_rambling():
    draft = "₹995.00 wapas aa chuka hai."
    assert reply_problem("aapko lagta hai ki " * 20, draft, "hinglish") == "repeats itself"
    assert reply_problem("₹995.00 wapas aa chuka hai.", draft, "hi-IN") == "wrong language"
    assert reply_problem("x" * 1000, draft, "hinglish") == "too long"
    assert reply_problem("₹995.00 वापस आ चुका है।", draft, "hi-IN") is None
    assert strip_preamble("ज़रूर, मैं इसे हिंदी में लिखता हूँ:\n₹995.00 वापस आ चुका है।") == "₹995.00 वापस आ चुका है।"


# -- answers ---------------------------------------------------------------------------------------


def test_without_any_model_the_checked_answer_carries_the_real_figures(rt):
    a = MerchantAssistant(rt, HERO, chain())
    r = a.reply("Kitna paisa wapas aaya?", "hinglish")
    m = rt.metrics(HERO)
    assert r["source"] == "template"
    assert inr(m["identified_paise"]) in r["reply"] and inr(m["recovered_paise"]) in r["reply"]


def test_an_invented_amount_never_reaches_the_merchant(rt):
    sarvam = FakeSarvam("Aapko ₹12,34,567.00 wapas milenge kal tak!")
    r = MerchantAssistant(rt, HERO, chain(sarvam=sarvam)).reply("Kitna paisa wapas aaya?", "hinglish")
    assert r["source"] == "template" and "12,34,567" not in r["reply"]
    assert "not in the records" in r["note"]


def test_a_grounded_model_reply_is_used_and_sees_only_this_merchants_facts(rt):
    m = rt.metrics(HERO)
    sarvam = FakeSarvam(f"Namaste! {inr(m['recovered_paise'])} aapke account mein wapas aa gaya hai.")
    r = MerchantAssistant(rt, HERO, chain(sarvam=sarvam)).reply("Kitna paisa wapas aaya?", "hinglish")
    assert r["source"] == "sarvam:fake"
    prompt = sarvam.calls[0][1][-1]["content"]
    other = [x.merchant_id for x in rt.dataset.merchants if x.merchant_id != HERO]
    assert not any(mid in prompt for mid in other)


def test_a_local_translation_must_keep_every_figure(rt):
    m = rt.metrics(HERO)
    faithful = FakeLocal(lambda msgs: "हमने आपके सभी सेटलमेंट जांचे। " + " ".join(
        f"राशि {a} दर्ज है।" for a in re.findall(r"₹[0-9,]+\.[0-9]{2}", msgs[-1]["content"])))
    changed = FakeLocal(f"हमने {inr(m['recovered_paise'] + 100)} वापस किया।")
    ok = MerchantAssistant(rt, HERO, chain(local=faithful)).reply("paisa kitna wapas aaya", "hi-IN")
    bad = MerchantAssistant(rt, HERO, chain(local=changed)).reply("paisa kitna wapas aaya", "hi-IN")
    assert ok["source"] == "local:fake"
    assert bad["source"] == "template" and "changed a figure" in bad["note"]


def test_the_local_model_writes_replies_unless_switched_off(rt, monkeypatch):
    m = rt.metrics(HERO)
    reply = f"Aapka {inr(m['recovered_paise'])} wapas aa gaya hai, bhai."
    local = FakeLocal(reply)
    r = MerchantAssistant(rt, HERO, chain(local=local)).reply("Kitna paisa wapas aaya?", "hinglish")
    assert r["source"] == "local:fake" and r["reply"] == reply
    monkeypatch.setenv("MFP_LOCAL_WRITES", "0")
    local = FakeLocal(reply)
    r = MerchantAssistant(rt, HERO, chain(local=local)).reply("Kitna paisa wapas aaya?", "hinglish")
    assert r["source"] == "template" and local.calls == []


def test_money_under_review_can_never_be_called_returned(rt):
    waiting = [c for c in rt.cases.all(HERO) if str(c.state) == "ESCALATED" and c.proof]
    assert waiting
    amount = inr(sum(c.proof.discrepancy_paise for c in waiting))  # the total the answer is built on
    wrong = FakeLocal(f"Koi baat nahi! {amount} aapke account mein wapas aa gaya hai.")
    right = FakeLocal(f"{amount} aapke review ke liye rakha gaya hai, approve ya dismiss karein.")
    bad = MerchantAssistant(rt, HERO, chain(local=wrong)).reply("Kya mujhe kuch karna hai?", "hinglish")
    good = MerchantAssistant(rt, HERO, chain(local=right)).reply("Kya mujhe kuch karna hai?", "hinglish")
    assert bad["source"] == "template" and "under review" in bad["note"]
    assert good["source"] == "local:fake"


def test_invented_counts_and_needless_amounts_are_rejected(rt):
    m = rt.metrics(HERO)
    invented = FakeLocal(f"{inr(m['recovered_paise'])} 24 ghante mein wapas aa gaya.")
    r = MerchantAssistant(rt, HERO, chain(local=invented)).reply("Kitna paisa wapas aaya?", "hinglish")
    assert r["source"] == "template" and "24" in r["note"]
    greeting = FakeLocal(f"Shukriya! Aapke {inr(m['recovered_paise'])} wapas aa gaye.")
    r = MerchantAssistant(rt, HERO, chain(local=greeting)).reply("thanks bhai", "hinglish")
    assert r["source"] == "template" and "₹" not in r["reply"]


def test_an_english_question_gets_an_english_reply(rt):
    local = FakeLocal("Aapke payments late aaye hain, abhi sab aa gaya hai aur kuch bhi missing nahi.")
    r = MerchantAssistant(rt, HERO, chain(local=local)).reply("Why are my settlements late?", "auto")
    assert r["language"] == "en-IN" and r["source"] == "template" and "wrong language" in r["note"]


def test_the_local_model_understands_what_keywords_miss(rt):
    local = FakeLocal(topic="rental")
    r = MerchantAssistant(rt, HERO, chain(local=local)).reply("मशीन लौटाई थी फिर भी पैसे गए", "hinglish")
    assert r["topics"] == ["rental"] and r["understood_by"] == "local:fake"


def test_credentials_are_refused_instantly_without_asking_a_model(rt):
    sarvam, local = FakeSarvam("ok"), FakeLocal("ok", topic="summary")
    r = MerchantAssistant(rt, HERO, chain(sarvam, local)).reply("mera OTP 4321 hai, check karo", "auto")
    assert r["source"] == "safety" and "OTP" in r["reply"]
    assert sarvam.calls == [] and local.calls == []


# -- adapters -------------------------------------------------------------------------------------


def test_sarvam_requests_follow_the_published_api(monkeypatch):
    sent = []

    def fake_post(url, body, headers, timeout):
        sent.append((url, body, headers))
        if url.endswith("/v1/chat/completions"):
            return {"choices": [{"message": {"content": " jawab "}}]}
        if url.endswith("/translate"):
            return {"translated_text": "अनुवाद"}
        return {"audios": ["UklGRg=="]}

    monkeypatch.setattr(backends_module, "_post", fake_post)
    client = SarvamClient(api_key="sk_test")
    assert SarvamChat(client).complete("sys", [{"role": "user", "content": "q"}]) == "jawab"
    assert client.translate("hello", "hi-IN") == "अनुवाद"
    assert client.speak("namaste", "hi-IN") == b"RIFF"
    chat_url, chat_body, headers = sent[0]
    assert chat_url == "https://api.sarvam.ai/v1/chat/completions" and headers["api-subscription-key"] == "sk_test"
    assert chat_body["messages"][0] == {"role": "system", "content": "sys"}
    assert sent[1][1]["target_language_code"] == "hi-IN" and "input" in sent[1][1]
    assert sent[2][1]["text"] == "namaste" and sent[2][1]["language_code"] == "hi-IN"


def test_unreachable_ollama_is_simply_unavailable():
    assert OllamaChat(url="http://127.0.0.1:9").available() is False
    assert BackendChain(SarvamChat(SarvamClient(api_key="")), OllamaChat(url="http://127.0.0.1:9")).status()[
        "active"] == "template"


def test_chat_api_answers_from_the_demo_runtime(loop_datasets, monkeypatch):
    from fastapi.testclient import TestClient

    from mfp.demo import server
    from mfp.demo.director import DemoDirector

    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.setenv("MFP_OLLAMA_URL", "http://127.0.0.1:9")
    server._state["d"] = DemoDirector(data_dir=loop_datasets[0].parent, seed=5)
    server._assistants.clear()
    client = TestClient(server.app)
    assert "Settlement Assistant" in client.get("/chat").text
    assert client.get("/api/chat/status").json()["active"] == "template"
    for _ in range(3):
        client.post("/api/demo/next")
    r = client.post("/api/chat", json={"message": "Kitna paisa wapas aaya?"}).json()
    assert r["source"] == "template" and "₹" in r["reply"]
    assert client.post("/api/chat", json={"message": "  "}).status_code == 400
    assert client.post("/api/chat/speak", json={"text": "namaste"}).status_code == 204
    server._state.clear()
    server._assistants.clear()


# -- Paytm speaks first, voice, impact ---------------------------------------------------------------


def test_paytm_messages_first_leading_with_money_returned(rt):
    m = rt.metrics(HERO)
    r = MerchantAssistant(rt, HERO, chain()).proactive("hinglish")
    assert r["proactive"] and r["source"] == "template" and r["understood_by"] == "paytm"
    first_sentence = r["reply"].split(". ")[0]
    assert inr(m["recovered_paise"]) in first_sentence and "wapas" in first_sentence
    assert "complaint" in r["reply"]


def test_saying_everything_is_settled_while_money_is_pending_is_rejected(rt):
    m = rt.metrics(HERO)
    assert m["escalated"] > 0
    local = FakeLocal(f"{inr(m['recovered_paise'])} wapas aa gaya, ab sab set ho gaya!")
    r = MerchantAssistant(rt, HERO, chain(local=local)).proactive("hinglish")
    assert r["source"] == "template" and "everything is settled" in r["note"]


def test_voice_notes_are_sent_to_sarvam_as_multipart(monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self):
            return json.dumps({"transcript": "soundbox ka rental kyun kata", "language_code": "hi-IN"}).encode()

    def fake_urlopen(request, timeout):
        captured["url"], captured["headers"], captured["body"] = request.full_url, dict(request.headers), request.data
        return Response()

    monkeypatch.setattr(backends_module.urllib.request, "urlopen", fake_urlopen)
    out = SarvamClient(api_key="sk_test").transcribe(b"OggS-audio", "audio/webm;codecs=opus")
    assert out == {"transcript": "soundbox ka rental kyun kata", "language_code": "hi-IN"}
    assert captured["url"] == "https://api.sarvam.ai/speech-to-text"
    assert captured["headers"]["Content-type"].startswith("multipart/form-data; boundary=")
    assert b'name="file"; filename="speech.webm"' in captured["body"] and b"OggS-audio" in captured["body"]
    assert b'name="model"' in captured["body"]


def test_voice_and_impact_endpoints(loop_datasets, monkeypatch):
    from fastapi.testclient import TestClient

    from mfp.demo import server
    from mfp.demo.director import DemoDirector

    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.setenv("MFP_OLLAMA_URL", "http://127.0.0.1:9")
    server._state["d"] = DemoDirector(data_dir=loop_datasets[0].parent, seed=5)
    server._assistants.clear()
    client = TestClient(server.app)
    assert client.get("/api/impact").status_code == 409
    assert client.post("/api/chat/proactive", json={}).status_code == 409
    for _ in range(3):
        client.post("/api/demo/next")
    impact = client.get("/api/impact").json()
    metrics = client.get("/api/state").json()["metrics"]
    assert impact["identified_paise"] == metrics["identified_paise"] and impact["complaints_raised"] == 0
    assert impact["lines_checked"] > 0 and impact["cases"] == metrics["proven_cases"] + metrics["escalated"]
    assert client.post("/api/chat/proactive", json={"language": "en-IN"}).json()["proactive"] is True
    assert client.post("/api/chat/listen", content=b"audio", headers={"Content-Type": "audio/webm"}).status_code == 204
    server._state.clear()
    server._assistants.clear()


# -- the merchant resolves from the chat ---------------------------------------------------------------


@pytest.fixture()
def fresh(loop_datasets):
    root = loop_datasets[0]
    as_of = json.loads((root / "manifest.json").read_text(encoding="utf-8"))["as_of"]
    runtime = Runtime(root, start=f"{as_of}T18:00:00")
    runtime.connect(HERO)
    runtime.run_until_quiet(60)
    return runtime


def test_review_cases_come_to_the_merchant_as_decisions(fresh):
    a = MerchantAssistant(fresh, HERO, chain())
    r = a.reply("Kya mujhe kuch karna hai?", "hinglish")
    waiting = fresh.cases.in_state(CaseState.ESCALATED, merchant_id=HERO)
    assert len(r["actions"]) == len(waiting) > 0
    card = r["actions"][0]
    assert {o["action"] for o in card["options"]} == {"file", "dismiss"} and card["why"] and card["amount_paise"] > 0
    assert a.proactive("hinglish")["actions"], "Paytm's first message carries the decisions too"


def test_the_merchant_files_or_dismisses_and_the_agents_carry_it_out(fresh):
    a = MerchantAssistant(fresh, HERO, chain())
    first, second = [c["case_id"] for c in a.review_cards()[:2]]
    filed = a.decide(first, "file")
    case = fresh.cases.get(first)
    assert case.claim_id and "(merchant, via chat)" in case.human_attestation["reviewer"]
    assert filed["claim_id"] == case.claim_id and "file" in filed["reply"]
    assert all(c["case_id"] != first for c in filed["actions"])
    dismissed = a.decide(second, "dismiss")
    assert fresh.cases.get(second).state is CaseState.CLOSED and "band" in dismissed["reply"]
    with pytest.raises(ValueError):
        a.decide(first, "file")  # already decided


def test_a_merchant_cannot_decide_another_merchants_case(fresh):
    other = next(m.merchant_id for m in fresh.dataset.merchants if m.merchant_id != HERO and m.fidelity == "FULL")
    fresh.connect(other)
    case_id = MerchantAssistant(fresh, HERO, chain()).review_cards()[0]["case_id"]
    with pytest.raises(KeyError):
        MerchantAssistant(fresh, other, chain()).decide(case_id, "file")


def test_a_reported_problem_the_records_do_not_show_becomes_a_ticket(fresh):
    quiet = next(mid for mid in fresh.index.merchant_ids("FULL") if mid != HERO and not any(
        c.component == "RENTAL" for c in fresh.cases.all(mid)))
    fresh.connect(quiet)
    r = MerchantAssistant(fresh, quiet, chain()).reply("maine soundbox wapas kar diya phir bhi rental kat raha hai", "auto")
    assert r["ticket"]["status"] == "OPEN" and r["ticket"]["ticket_id"] in r["reply"]
    assert fresh.tickets.for_merchant(quiet, "OPEN") and fresh.events.of_kind("ticket.opened")
    fresh.tickets.resolve(r["ticket"]["ticket_id"], "Ops", "Pickup confirmed; rental reversed manually")
    assert not fresh.tickets.for_merchant(quiet, "OPEN")


def test_a_reported_problem_already_caught_shows_its_status_instead(fresh):
    rental = [c for c in fresh.cases.all(HERO) if c.component == "RENTAL" and c.proof]
    if not rental:
        pytest.skip("loop dataset has no rental case for the hero")
    r = MerchantAssistant(fresh, HERO, chain()).reply("soundbox wapas kar diya phir bhi rental kata", "hinglish")
    assert "ticket" not in r and "₹" in r["reply"]


def test_decide_and_ticket_endpoints(loop_datasets, monkeypatch):
    from fastapi.testclient import TestClient

    from mfp.demo import server
    from mfp.demo.director import DemoDirector

    monkeypatch.delenv("SARVAM_API_KEY", raising=False)
    monkeypatch.setenv("MFP_OLLAMA_URL", "http://127.0.0.1:9")
    server._state["d"] = DemoDirector(data_dir=loop_datasets[0].parent, seed=5)
    server._assistants.clear()
    client = TestClient(server.app)
    client.post("/api/live/select", json={"merchant_id": HERO})
    client.post("/api/clock/advance?days=20")
    cards = client.post("/api/chat", json={"message": "Kya mujhe kuch karna hai?"}).json()["actions"]
    assert cards
    decided = client.post("/api/chat/decide", json={"case_id": cards[0]["case_id"], "action": "dismiss"})
    assert decided.status_code == 200
    assert client.post("/api/chat/decide", json={"case_id": cards[0]["case_id"], "action": "dismiss"}).status_code == 409
    assert client.post("/api/chat/decide", json={"case_id": "CASE-99999", "action": "file"}).status_code == 404
    assert client.post("/api/chat/decide", json={"case_id": cards[0]["case_id"], "action": "pay me"}).status_code == 400
    assert client.get("/api/tickets").json() == []
    server._state.clear()
    server._assistants.clear()


# -- merchant power: payment lookup, proof, a person, appeal, replies that come back --------------------


def closed_case(rt):
    """A case closed without recovery. The short test dataset has none (nothing is older than the
    180-day window), so one is set up directly; the appeal path is what is under test."""
    existing = [c for c in rt.cases.all(HERO) if c.state is CaseState.CLOSED_UNRECOVERED]
    if existing:
        return existing
    case = next(c for c in rt.cases.all(HERO) if c.state is CaseState.RECOVERED)
    case.state, case.recovered_paise, case.refund_credit = CaseState.CLOSED_UNRECOVERED, 0, None
    return [case]


def test_payment_lookup_finds_one_payment_by_amount_and_date(fresh):
    from mfp.assistant.chat import parse_payment_query

    assert parse_payment_query("08/09 wala 2113 ka payment kab aayega", 2026)[0] == {211300}
    assert parse_payment_query("Settlement late kyun aaya?", 2026) is None
    view = fresh.view(HERO)
    settled = next(t for t in reversed(view.transactions) if str(t.kind) == "PAYMENT" and str(t.status) == "SUCCESS"
                   and view.lines_by_txn.get(t.txn_id))
    q = f"{settled.captured_at:%d %b} ka ₹{settled.amount_paise / 100:,.2f} ka payment kahan hai?"
    r = MerchantAssistant(fresh, HERO, chain(local=FakeLocal("should not be used"))).reply(q, "hinglish")
    assert r["topics"] == ["payment"] and r["source"] == "template"
    match = next(p for p in r["payments"] if p["txn_id"] == settled.txn_id)
    assert match["status"] in ("SETTLED", "LATE", "NOT_DUE") and (match["status"] == "NOT_DUE" or match["utr"])


def test_proof_card_shows_rule_amount_and_refund_credit(fresh):
    recovered = next(c for c in fresh.cases.all(HERO) if c.refund_credit)
    r = MerchantAssistant(fresh, HERO, chain()).reply(f"{recovered.case_id} ka proof dikhao", "hinglish")
    p = r["proof"]
    assert p["case_id"] == recovered.case_id and p["verdict"] == "PROVEN" and p["rule"] and p["source"]
    assert p["credit"]["utr"] == recovered.refund_credit["utr"] and inr(p["overcharge_paise"]) in r["reply"]


def test_asking_for_a_person_hands_over_the_conversation_and_the_reply_comes_back(fresh):
    a = MerchantAssistant(fresh, HERO, chain())
    history = [{"role": "user", "content": "rental kyun kata"}, {"role": "assistant", "content": "₹995 wapas aaya"}]
    r = a.reply("mujhe kisi insaan se baat karni hai", "hinglish", history)
    ticket = r["ticket"]
    assert ticket["topic"] == "handoff" and len(ticket["transcript"]) == 3
    again = a.reply("koi insaan se baat karao", "hinglish")
    assert again["ticket"]["ticket_id"] == ticket["ticket_id"], "no duplicate hand-offs"
    cursor = fresh.inbox.latest_id()
    fresh.tickets.resolve(ticket["ticket_id"], "Ops Priya", "Namaste, main Priya. Aapka rental wapas aa chuka hai.")
    reply = fresh.inbox.since(HERO, cursor)[-1]
    assert reply.kind == "ticket.reply" and "Priya" in reply.text_hi


def test_refunds_landing_are_announced_in_the_chat(fresh):
    credited = [m for m in fresh.inbox.since(HERO) if m.kind == "refund.credited"]
    recovered = [c for c in fresh.cases.all(HERO) if c.refund_credit]
    assert len(credited) == len(recovered) > 0
    assert all(m.ref["utr"] and "UTR" in m.text_hi for m in credited)


def test_a_merchant_can_appeal_a_closed_case_and_hears_the_outcome(fresh):
    closed = closed_case(fresh)
    a = MerchantAssistant(fresh, HERO, chain())
    r = a.reply("main is faisle se sehmat nahi, appeal karna hai", "hinglish")
    assert r["topics"] == ["appeal"] and {c["case_id"] for c in r["appealable"]} == {c.case_id for c in closed}
    case = closed[0]
    a.appeal(case.case_id, "Dispute window ke andar tha")
    assert case.state is CaseState.ESCALATED and case.escalation["appeal"]
    assert all(c["case_id"] != case.case_id for c in a.review_cards()), "appeals are the ops desk's decision"
    cursor = fresh.inbox.latest_id()
    fresh.review(case.case_id, "dismiss", "Settlement ops desk", "Window 180 din ka hai")
    outcome = fresh.inbox.since(HERO, cursor)[-1]
    assert outcome.kind == "appeal.decided" and outcome.ref["outcome"] == "upheld"


def test_only_a_person_can_reopen_a_closed_case(fresh):
    from mfp.cases.state_machine import IllegalTransition

    closed = closed_case(fresh)[0]
    from mfp.core.enums import Actor

    with pytest.raises(IllegalTransition):
        fresh.state_machine.transition(closed, CaseState.ESCALATED, Actor.FOLLOWUP_AGENT, "agent reopening")
