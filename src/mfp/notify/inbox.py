"""The merchant's inbox: messages Paytm sends into the chat without being asked.

Refunds landing in the bank, replies from Paytm's team on a ticket, and the
outcome of an appeal all arrive here, so the conversation continues after the
merchant has left it. The chat polls for anything newer than what it has shown.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from mfp.core.clock import Clock


@dataclass(frozen=True)
class InboxMessage:
    msg_id: int
    merchant_id: str
    at: str
    kind: str            # refund.credited | ticket.reply | appeal.decided
    text_hi: str         # Hinglish
    text_en: str
    ref: dict[str, Any] = field(default_factory=dict)

    def summary(self) -> dict[str, Any]:
        return {"msg_id": self.msg_id, "at": self.at, "kind": self.kind, "text_hi": self.text_hi,
                "text_en": self.text_en, "ref": self.ref}


class MerchantInbox:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._messages: list[InboxMessage] = []

    def post(self, merchant_id: str, kind: str, text_hi: str, text_en: str, **ref: Any) -> InboxMessage:
        message = InboxMessage(len(self._messages) + 1, merchant_id, self.clock.now().isoformat(), kind,
                               text_hi, text_en, ref)
        self._messages.append(message)
        return message

    def since(self, merchant_id: str, after_id: int = 0) -> list[InboxMessage]:
        return [m for m in self._messages if m.merchant_id == merchant_id and m.msg_id > after_id]

    def latest_id(self) -> int:
        return len(self._messages)
