from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Optional

logger = logging.getLogger(__name__)


class EmailSender(ABC):
    """Provider-agnostic transactional email sender.

    IMPLEMENTATION_PLAN.md's Open Questions leaves the diff-email provider (Postmark vs. Resend)
    deliberately unpicked -- both offer a single-POST transactional send API, so whichever gets
    chosen at implementation time is a small, swappable adapter (one new subclass of this class),
    not a design fork. Nothing in this repo calls a real provider yet -- see LoggingEmailSender
    below, the only concrete implementation for now.
    """

    @abstractmethod
    def send(self, subject: str, html_body: str, *, to: Optional[str] = None) -> None:
        """Send one transactional email. `to` is the merchant/notification recipient address --
        optional because no config field currently models it (out of this commit's scope); a
        real provider implementation would require it."""
        ...


class LoggingEmailSender(EmailSender):
    """The deliberately-deferred provider choice from IMPLEMENTATION_PLAN.md's Open Questions
    (Postmark vs. Resend, neither picked yet) -- this is NOT a real provider integration. It
    makes zero network calls and zero external side effects: it only logs what it would have
    sent, so it is safe to wire into every pipeline run today (satisfying the "zero AWS/network
    cost" testing constraint) without creating a real email-sending side effect. Swapping in a
    real provider later is a one-class change: implement EmailSender.send against Postmark's or
    Resend's HTTP API in a new subclass and change the one call site that constructs an
    EmailSender -- no caller-side changes required.
    """

    def send(self, subject: str, html_body: str, *, to: Optional[str] = None) -> None:
        logger.info(
            "LoggingEmailSender: would send email to=%s subject=%r body_length=%d",
            to, subject, len(html_body),
        )
