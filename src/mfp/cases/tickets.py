"""Merchant-reported issues the records do not (yet) show.

When a merchant tells the chat about a problem and no case already covers it,
the teammate does not promise money it cannot prove. It opens a ticket for
Paytm's team with the merchant's own words and what the records showed, and
the merchant is told exactly that. A person closes the ticket.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mfp.core.clock import Clock
from mfp.core.enums import Actor
from mfp.core.events import EventLog


@dataclass
class Ticket:
    ticket_id: str
    merchant_id: str
    topic: str
    statement: str               # the merchant's own words
    records_showed: str          # what the teammate found when it checked
    opened_at: str
    status: str = "OPEN"         # OPEN | RESOLVED
    resolution: str | None = None
    resolved_by: str | None = None
    case_id: str | None = None
    transcript: list[dict[str, str]] = field(default_factory=list)   # the chat so far, for a hand-off
    history: list[dict[str, Any]] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        return {k: v for k, v in self.__dict__.items() if k != "history"}


class TicketDesk:
    def __init__(self, clock: Clock, events: EventLog, inbox=None) -> None:
        self.clock = clock
        self.events = events
        self.inbox = inbox
        self._tickets: dict[str, Ticket] = {}

    def open(self, merchant_id: str, topic: str, statement: str, records_showed: str, *,
             case_id: str | None = None, transcript: list[dict[str, str]] | None = None) -> Ticket:
        ticket = Ticket(f"TKT-{len(self._tickets) + 1:05d}", merchant_id, topic, statement.strip()[:500],
                        records_showed, self.clock.now().isoformat(), case_id=case_id,
                        transcript=[{"role": t.get("role", "user"), "content": str(t.get("content", ""))[:400]}
                                    for t in (transcript or [])][-8:])
        self._tickets[ticket.ticket_id] = ticket
        self.events.append(Actor.SYSTEM, "ticket.opened", merchant_id=merchant_id, ticket_id=ticket.ticket_id,
                           topic=topic, statement=ticket.statement, records_showed=records_showed,
                           reason={"handoff": "Merchant asked to talk to a person; conversation handed to Paytm's team"}
                           .get(topic, "Merchant reported an issue the records do not show; routed to Paytm's team"))
        return ticket

    def resolve(self, ticket_id: str, resolved_by: str, resolution: str) -> Ticket:
        ticket = self._tickets[ticket_id]
        if ticket.status != "OPEN":
            raise ValueError(f"{ticket_id} is already {ticket.status}")
        ticket.status, ticket.resolved_by, ticket.resolution = "RESOLVED", resolved_by, resolution.strip()[:500]
        self.events.append(Actor.HUMAN, "ticket.resolved", merchant_id=ticket.merchant_id, ticket_id=ticket_id,
                           resolved_by=resolved_by, resolution=ticket.resolution)
        if self.inbox is not None:
            self.inbox.post(ticket.merchant_id, "ticket.reply",
                            f"Paytm team ka jawab (ticket {ticket_id}): {ticket.resolution}",
                            f"Reply from Paytm's team (ticket {ticket_id}): {ticket.resolution}",
                            ticket_id=ticket_id, resolved_by=resolved_by)
        return ticket

    def open_for(self, merchant_id: str, topic: str) -> Ticket | None:
        return next((t for t in self._tickets.values()
                     if t.merchant_id == merchant_id and t.topic == topic and t.status == "OPEN"), None)

    def for_merchant(self, merchant_id: str, status: str | None = None) -> list[Ticket]:
        return [t for t in self._tickets.values()
                if t.merchant_id == merchant_id and (status is None or t.status == status)]

    def get(self, ticket_id: str) -> Ticket:
        return self._tickets[ticket_id]
