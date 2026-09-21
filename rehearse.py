"""Stage rehearsal: python rehearse.py [--seed 42]

One command before going on stage. It checks everything the live demo depends
on, warms the local model so the first chat is not a minute-long wait, runs the
whole twelve-step story once, and asks the merchant chat three questions.
Prints a checklist; exits non-zero if anything the demo needs is broken.
Optional extras (Sarvam, n8n) are reported but never fail the rehearsal.
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from mfp.assistant.backends import OllamaChat  # noqa: E402
from mfp.assistant.chat import MerchantAssistant  # noqa: E402
from mfp.demo.director import DemoDirector  # noqa: E402

results: list[tuple[str, str, str]] = []  # (status, check, detail)


def record(status: str, check: str, detail: str = "") -> None:
    results.append((status, check, detail))
    mark = {"ok": "✓", "warn": "!", "fail": "✗"}[status]
    print(f"  {mark} {check}" + (f": {detail}" if detail else ""), flush=True)


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=1):
            return True
    except OSError:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    data = ROOT / "data" / "generated"

    print("\n1. Data")
    for name, extra in ((f"seed-{args.seed}", []), (f"seed-{args.seed}-baseline", ["--no-leakage"])):
        if (data / name / "manifest.json").exists():
            record("ok", f"{name} dataset present")
        else:
            print(f"    generating {name}…", flush=True)
            done = subprocess.run([sys.executable, str(ROOT / "generate.py"), "--seed", str(args.seed), *extra],
                                  capture_output=True, text=True)
            record("ok" if done.returncode == 0 else "fail", f"{name} dataset generated",
                   "" if done.returncode == 0 else done.stderr.strip()[-200:])

    sample = ROOT / "samples" / f"paytm_settlement_report_MER-0001_seed{args.seed}.csv"
    if sample.exists():
        record("ok", "sample Paytm settlement report present")
    else:
        done = subprocess.run([sys.executable, str(ROOT / "tools" / "export_paytm_report.py"), "--seed", str(args.seed)],
                              capture_output=True, text=True)
        record("ok" if done.returncode == 0 else "warn", "sample Paytm settlement report generated",
               "" if done.returncode == 0 else "the Import report tab's sample button will not work")

    print("\n2. The twelve-step story")
    started = time.perf_counter()
    director = DemoDirector(data_dir=data, seed=args.seed)
    try:
        steps = director.run_all()
    except Exception as exc:  # the rehearsal must report, not crash
        record("fail", "story ran", f"{type(exc).__name__}: {exc}")
        return summary()
    by_key = {s["key"]: s["data"] for s in steps}
    record("ok" if len(steps) == 12 else "fail", "all 12 steps ran", f"{time.perf_counter() - started:.0f}s")
    m = by_key["recovery"]["metrics"]
    record("ok" if m["recovered_paise"] > 0 else "fail", "money recovered",
           f"₹{m['recovered_paise'] / 100:,.2f} of ₹{m['identified_paise'] / 100:,.2f}")
    record("ok" if by_key["proof"]["proof"]["verdict"] == "PROVEN" else "fail", "showcase case is PROVEN")
    record("ok" if by_key["red_team"]["false_claims"] == 0 else "fail", "red team: zero false corrections",
           f"{by_key['red_team']['generated']} scenarios")
    base = by_key["baseline"]
    record("ok" if (base["proven_cases"], base["claims_filed"]) == (0, 0) else "fail", "clean baseline finds nothing")

    print("\n3. Merchant chat")
    local = OllamaChat()
    if local.available():
        print(f"    warming {local.model}…", flush=True)
        t = time.perf_counter()
        local.warm()
        record("ok", f"local model {local.model} loaded", f"{time.perf_counter() - t:.0f}s")
    else:
        record("warn", "local model not running", "chat will use checked answers; start Ollama and "
               f"`ollama pull {local.model}` for AI replies")
    assistant = MerchantAssistant(director.rt, director.merchant_id)
    for question, language in (("Soundbox rental kyun kata?", "auto"), ("Why are my settlements late?", "auto"),
                               ("मेरा पैसा कितना वापस आया?", "auto")):
        t = time.perf_counter()
        r = assistant.reply(question, language)
        ok = bool(r["reply"]) and "₹" in r["reply"]
        record("ok" if ok else "fail", f"chat: {question}",
               f"{r['source']}, {time.perf_counter() - t:.1f}s" + (f" ({r['note']})" if r["note"] else ""))
    first = assistant.proactive("hinglish")
    record("ok" if "₹" in first["reply"] else "fail", "Paytm's first message", first["source"])

    print("\n4. Optional integrations")
    record("ok" if os.environ.get("SARVAM_API_KEY") else "warn", "Sarvam",
           "key set" if os.environ.get("SARVAM_API_KEY") else "no SARVAM_API_KEY: local model and browser voice are used")
    if os.environ.get("MFP_WORKFLOW") == "n8n":
        record("ok" if port_open("localhost", 5678) else "fail", "n8n reachable on :5678")
    else:
        record("warn" if not port_open("localhost", 5678) else "ok", "n8n",
               "running on :5678 (set MFP_WORKFLOW=n8n to use it)" if port_open("localhost", 5678)
               else "not running; the local workflow is used")

    print("\n5. Server")
    if port_open("127.0.0.1", 8000):
        try:
            with urllib.request.urlopen("http://127.0.0.1:8000/api/state", timeout=3) as res:
                state = json.loads(res.read())
            fresh = not state["merchant"]["connected"]
            record("ok" if fresh else "warn", "command center is running on :8000",
                   "at the start of the story" if fresh else "mid-story: press Start over before presenting")
        except OSError:
            record("warn", "port 8000 is busy with something else", "use python serve.py --port 8010")
    else:
        record("warn", "command center not started", "run: python serve.py")
    return summary()


def summary() -> int:
    fails = [r for r in results if r[0] == "fail"]
    warns = [r for r in results if r[0] == "warn"]
    print("\n" + ("READY FOR STAGE" if not fails else "NOT READY") +
          f"  ({len(results) - len(fails) - len(warns)} ok, {len(warns)} notes, {len(fails)} failures)")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
