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
