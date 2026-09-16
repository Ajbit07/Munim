"""Case and institutional memory.

Three kinds of memory, one interface:

  case history         what was found, proven, claimed and how it ended
  merchant history     recurring discrepancies and past resolutions per merchant
  operational history  how the claims desk responds, and what overturns a rejection

LocalMemoryStore keeps all three in SQLite and retrieves similar cases by
weighted feature overlap. CogneeMemoryStore (mfp.memory.cognee_store) puts
case narratives into a Cognee knowledge graph and keeps the structured
operational statistics local.

Memory is advisory. It can change how a claim is filed -- which evidence to
attach, whether to pilot one claim before filing a batch -- and it can give
the Investigation Agent precedent. It can never change a proof.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Protocol


class MemoryStore(Protocol):
    name: str

    def record_case(self, summary: dict[str, Any]) -> None: ...
    def similar_cases(self, query: dict[str, Any], k: int = 3) -> list[dict[str, Any]]: ...
    def merchant_history(self, merchant_id: str) -> dict[str, Any]: ...
    def record_response(self, pattern: str, reason_code: str | None, attachments: list[str], outcome: str) -> None: ...
    def winning_attachments(self, pattern: str) -> set[str]: ...
    def pattern_outcomes(self, pattern: str) -> dict[str, int]: ...


_FEATURE_WEIGHTS = {"pattern": 4, "discrepancy_type": 2, "instrument": 2, "rule_id": 2, "merchant_id": 1, "root_cause": 3}


class LocalMemoryStore:
    name = "local-sqlite"

    def __init__(self, path: Path | str = ":memory:") -> None:
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS cases (
                case_id TEXT PRIMARY KEY, merchant_id TEXT, discrepancy_type TEXT, pattern TEXT,
                instrument TEXT, rule_id TEXT, month TEXT, state TEXT, proven_paise INTEGER,
                recovered_paise INTEGER, root_cause TEXT, outcome TEXT, body TEXT);
            CREATE TABLE IF NOT EXISTS responses (
                pattern TEXT, reason_code TEXT, attachments TEXT, outcome TEXT);
        """)

    def record_case(self, summary: dict[str, Any]) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO cases VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (summary["case_id"], summary["merchant_id"], summary["discrepancy_type"], summary["pattern"],
             summary.get("instrument"), summary.get("rule_id"), summary.get("month"), summary.get("state"),
             summary.get("proven_paise", 0), summary.get("recovered_paise", 0), summary.get("root_cause"),
             summary.get("outcome"), json.dumps(summary, default=str)),
        )
        self.db.commit()

    def similar_cases(self, query: dict[str, Any], k: int = 3) -> list[dict[str, Any]]:
        rows = self.db.execute(
            "SELECT body FROM cases WHERE pattern = ? OR discrepancy_type = ? OR merchant_id = ?",
            (query.get("pattern"), query.get("discrepancy_type"), query.get("merchant_id")),
        ).fetchall()
        scored = []
        for (body,) in rows:
            past = json.loads(body)
            if past["case_id"] == query.get("case_id"):
                continue
            score = sum(w for f, w in _FEATURE_WEIGHTS.items() if query.get(f) and past.get(f) == query.get(f))
            if past.get("outcome"):
                score += 1  # resolved precedent is worth more than an open case
            scored.append((score, past.get("month") or "", past))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return [dict(p, similarity=s) for s, _, p in scored[:k]]

    def merchant_history(self, merchant_id: str) -> dict[str, Any]:
        rows = self.db.execute(
            "SELECT pattern, COUNT(*), SUM(proven_paise), SUM(recovered_paise) FROM cases "
            "WHERE merchant_id = ? GROUP BY pattern", (merchant_id,)).fetchall()
        return {p: {"cases": n, "proven_paise": pv or 0, "recovered_paise": rv or 0} for p, n, pv, rv in rows}

    def record_response(self, pattern: str, reason_code: str | None, attachments: list[str], outcome: str) -> None:
        self.db.execute("INSERT INTO responses VALUES (?,?,?,?)",
                        (pattern, reason_code, json.dumps(sorted(attachments)), outcome))
        self.db.commit()

    def winning_attachments(self, pattern: str) -> set[str]:
        """Attachments present on approvals that were absent on earlier rejections."""
        rows = self.db.execute("SELECT attachments, outcome FROM responses WHERE pattern = ?", (pattern,)).fetchall()
        rejected = [set(json.loads(a)) for a, o in rows if o == "REJECTED"]
        approved = [set(json.loads(a)) for a, o in rows if o in ("APPROVED", "PARTIALLY_APPROVED")]
        if not rejected or not approved:
            return set()
        common_rejected = set.intersection(*rejected)
        winning: set[str] = set()
        for attachments in approved:
            winning |= attachments - common_rejected
        return winning

    def pattern_outcomes(self, pattern: str) -> dict[str, int]:
        rows = self.db.execute("SELECT outcome, COUNT(*) FROM responses WHERE pattern = ? GROUP BY outcome",
                               (pattern,)).fetchall()
        return dict(rows)
