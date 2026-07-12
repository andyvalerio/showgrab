"""SMTP digest notifier (REQ-SG-021, REQ-SG-022).

Generic SMTP config — works with any provider (Gmail app password, any other
SMTP account), selected via use_tls (STARTTLS, e.g. port 587) or use_ssl
(implicit TLS, e.g. port 465). No credentials or provider defaults are
hardcoded here; the caller always supplies a SmtpConfig.
"""

from __future__ import annotations

import smtplib
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Callable

from ..core.digest import build_digest
from ..core.models import NotifyEvent


@dataclass
class SmtpConfig:
    host: str
    port: int
    username: str
    password: str
    from_addr: str
    to_addrs: list[str] = field(default_factory=list)
    use_tls: bool = True  # STARTTLS
    use_ssl: bool = False  # implicit TLS/SSL; mutually exclusive with use_tls


class SmtpError(RuntimeError):
    pass


class SmtpNotifier:
    def __init__(
        self,
        config: SmtpConfig,
        *,
        client_factory: Callable[[], object] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._config = config
        self._timeout = timeout
        self._client_factory = client_factory or self._default_client

    def send_digest(self, events: list[NotifyEvent]) -> bool:
        """Sends exactly one digest email when there are events; sends
        nothing and returns False when the list is empty (REQ-SG-021)."""
        digest = build_digest(events)
        if digest is None:
            return False
        subject, body = digest

        msg = EmailMessage()
        msg["Subject"] = subject
        msg["From"] = self._config.from_addr
        msg["To"] = ", ".join(self._config.to_addrs)
        msg.set_content(body)

        self._with_session(lambda server: server.send_message(msg))
        return True

    def test_connection(self) -> bool:
        self._with_session(lambda server: None)
        return True

    def _default_client(self):
        c = self._config
        if c.use_ssl:
            return smtplib.SMTP_SSL(c.host, c.port, timeout=self._timeout)
        return smtplib.SMTP(c.host, c.port, timeout=self._timeout)

    def _with_session(self, action: Callable[[object], None]) -> None:
        c = self._config
        server = self._client_factory()
        try:
            server.ehlo()
            if c.use_tls and not c.use_ssl:
                server.starttls()
                server.ehlo()
            if c.username:
                server.login(c.username, c.password)
            action(server)
        except smtplib.SMTPException as exc:
            raise SmtpError(f"SMTP operation failed: {exc}") from exc
        finally:
            try:
                server.quit()
            except Exception:
                pass
