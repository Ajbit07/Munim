"""The claim lifecycle executor.

    CREATE_CLAIM -> ATTACH_EVIDENCE -> SUBMIT -> WAIT -> CHECK_STATUS
        -> FOLLOW_UP -> RESPONSE -> RESOLVE / REPRESENT / ESCALATE

Two executors share one contract:

  LocalWorkflowEngine  runs the lifecycle in-process. Always available.
  N8nWorkflowEngine    hands each step to an n8n workflow over its webhook
                       (workflow/n8n/claim_lifecycle.json). If n8n cannot be
                       reached, the step runs locally and the fallback is
                       logged, so a dead container never stops a claim.

The case state machine stays authoritative in Python either way. n8n executes
steps; it does not decide what state a case is in.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Protocol

from mfp.core.clock import Clock
from mfp.core.enums import Actor
from mfp.core.events import EventLog
from mfp.workflow.claims import Claim, ClaimResponse, ClaimStatus, MockClaimsDesk


class WorkflowEngine(Protocol):
    name: str

    def submit(self, claim: Claim) -> str: ...
    def check(self, claim: Claim) -> ClaimResponse | None: ...
    def follow_up(self, claim: Claim) -> None: ...
    def represent(self, claim: Claim, added: set[str], note: str) -> str: ...


class LocalWorkflowEngine:
    name = "local"

    def __init__(self, desk: MockClaimsDesk, clock: Clock, events: EventLog) -> None:
        self.desk = desk
        self.clock = clock
        self.events = events

    def _step(self, claim: Claim, step: str, engine: str | None = None, **detail) -> None:
        record = {"at": self.clock.now().isoformat(), "step": step, "engine": engine or self.name, **detail}
        claim.steps.append(record)
        self.events.append(Actor.WORKFLOW_ENGINE, f"workflow.{step.lower()}", case_id=claim.case_id,
                           merchant_id=claim.merchant_id, claim_id=claim.claim_id, engine=record["engine"], **detail)

    def submit(self, claim: Claim, engine: str | None = None) -> str:
        claim.workflow = engine or self.name
        self._step(claim, "CREATE_CLAIM", engine, amount_paise=claim.amount_paise, proof_id=claim.proof_id)
        self._step(claim, "ATTACH_EVIDENCE", engine, attachments=sorted(claim.attachments))
        claim.attempts += 1
        reference = self.desk.receive(claim, self.clock.now())
        claim.status = ClaimStatus.SUBMITTED
        self._step(claim, "SUBMIT", engine, reference=reference, attempt=claim.attempts)
        self._step(claim, "WAIT", engine)
        return reference

    def check(self, claim: Claim, engine: str | None = None) -> ClaimResponse | None:
        response = self.desk.check(claim.claim_id, self.clock.now())
        self._step(claim, "CHECK_STATUS", engine, responded=response is not None)
        if response is not None:
            claim.responses.append(response)
            claim.status = response.status
            self._step(claim, "RESPONSE", engine, status=str(response.status), reason_code=response.reason_code,
                       approved_paise=response.approved_paise)
        return response

    def follow_up(self, claim: Claim, engine: str | None = None) -> None:
        self.desk.chase(claim.claim_id, self.clock.now())
        self._step(claim, "FOLLOW_UP", engine)

    def withdraw(self, claim: Claim, reason: str, engine: str | None = None) -> None:
        self.desk.withdraw(claim.claim_id)
        claim.status = ClaimStatus.WITHDRAWN
        self._step(claim, "WITHDRAW", engine, reason=reason)

    def represent(self, claim: Claim, added: set[str], note: str, engine: str | None = None) -> str:
        claim.attachments |= added
        self._step(claim, "REPRESENT", engine, added=sorted(added), note=note)
        return self.submit(claim, engine)


class N8nWorkflowEngine:
    """Delegates lifecycle steps to n8n. The desk itself is served by the MFP API."""

    name = "n8n"

    def __init__(self, local: LocalWorkflowEngine, webhook_url: str | None = None, timeout: float = 10.0) -> None:
        self.local = local
        self.webhook_url = webhook_url or os.environ.get("N8N_WEBHOOK_URL", "http://localhost:5678/webhook/mfp-claim-lifecycle")
        self.timeout = timeout
        self.available = True

    def _call(self, action: str, claim: Claim, **extra) -> dict | None:
        if not self.available:
            return None
        body = json.dumps({"action": action, "claim_id": claim.claim_id, "case_id": claim.case_id,
                           "merchant_id": claim.merchant_id, "amount_paise": claim.amount_paise,
                           "attachments": sorted(claim.attachments), **extra}).encode("utf-8")
        request = urllib.request.Request(self.webhook_url, data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                reply = json.loads(response.read() or b"{}")
            self.local.events.append(Actor.WORKFLOW_ENGINE, "workflow.n8n.step", case_id=claim.case_id,
                                     merchant_id=claim.merchant_id, claim_id=claim.claim_id, action=action,
                                     engine="n8n", executed_by=reply.get("executed_by"))
            return reply
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            self.available = False
            self.local.events.append(Actor.WORKFLOW_ENGINE, "workflow.fallback", case_id=claim.case_id,
                                     merchant_id=claim.merchant_id, engine="n8n", fallback="local",
                                     error=type(exc).__name__)
            return None

    def _engine(self, reply: dict | None) -> str:
        return "n8n" if reply is not None else "local (n8n unreachable)"

    def submit(self, claim: Claim) -> str:
        return self.local.submit(claim, self._engine(self._call("submit", claim)))

    def check(self, claim: Claim) -> ClaimResponse | None:
        return self.local.check(claim, self._engine(self._call("check", claim)))

    def follow_up(self, claim: Claim) -> None:
        self.local.follow_up(claim, self._engine(self._call("follow_up", claim)))

    def represent(self, claim: Claim, added: set[str], note: str) -> str:
        return self.local.represent(claim, added, note, self._engine(self._call("represent", claim, added=sorted(added))))

    def withdraw(self, claim: Claim, reason: str) -> None:
        self.local.withdraw(claim, reason, self._engine(self._call("withdraw", claim)))
