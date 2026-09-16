"""Cognee-backed case memory.

Case narratives go into a Cognee knowledge graph, so precedent retrieval can
follow relationships (merchant -> root cause -> rule -> outcome) rather than
matching fields. Structured counterparty statistics -- which attachment
overturned which rejection -- stay in the local SQLite store, where exact
counting matters more than semantic recall.

Cognee's own pipeline needs an LLM provider configured in its environment.
Every call is guarded: if Cognee is not installed, not configured, or fails,
the store degrades to LocalMemoryStore for that call and says so. The
integration is written against Cognee's documented async API
(cognee.add / cognee.cognify / cognee.search) and has NOT been exercised
against a live Cognee instance in this build.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from mfp.memory.store import LocalMemoryStore

DATASET = "merchant_protection_cases"


class CogneeMemoryStore:
    def __init__(self, local: LocalMemoryStore | None = None) -> None:
        self.local = local or LocalMemoryStore()
        self.errors: list[str] = []
        try:
            import cognee  # noqa: F401
            self._available = True
            self.name = "cognee"
        except ImportError:
            self._available = False
            self.name = "local-sqlite (cognee not installed)"
        self._pending = 0

    def _run(self, coro):
        try:
            return asyncio.run(coro)
        except Exception as exc:  # any Cognee/LLM failure degrades to local memory
            self.errors.append(f"{type(exc).__name__}: {exc}")
            self._available = False
            self.name = "local-sqlite (cognee failed)"
            return None

    @staticmethod
    def _narrative(summary: dict[str, Any]) -> str:
        return (f"Case {summary['case_id']} for merchant {summary['merchant_id']}: {summary['pattern']} "
                f"({summary['discrepancy_type']}) on {summary.get('instrument')} in {summary.get('month')}, "
                f"rule {summary.get('rule_id')}, state {summary.get('state')}, root cause {summary.get('root_cause')}, "
                f"outcome {summary.get('outcome')}.")

    def record_case(self, summary: dict[str, Any]) -> None:
        self.local.record_case(summary)
        if self._available:
            import cognee
            self._run(cognee.add(self._narrative(summary), dataset_name=DATASET))
            self._pending += 1

    def similar_cases(self, query: dict[str, Any], k: int = 3) -> list[dict[str, Any]]:
        local = self.local.similar_cases(query, k)
        if not self._available or not self._pending:
            return local
        import cognee
        self._run(cognee.cognify())
        self._pending = 0
        text = f"Past cases similar to {query.get('pattern')} on {query.get('instrument')} for {query.get('merchant_id')}"
        results = self._run(cognee.search(query_text=text))
        if results:
            return [dict(p, graph_context=json.dumps(results[:3], default=str)[:500]) for p in local]
        return local

    def merchant_history(self, merchant_id: str) -> dict[str, Any]:
        return self.local.merchant_history(merchant_id)

    def record_response(self, pattern: str, reason_code: str | None, attachments: list[str], outcome: str) -> None:
        self.local.record_response(pattern, reason_code, attachments, outcome)

    def winning_attachments(self, pattern: str) -> set[str]:
        return self.local.winning_attachments(pattern)

    def pattern_outcomes(self, pattern: str) -> dict[str, int]:
        return self.local.pattern_outcomes(pattern)
