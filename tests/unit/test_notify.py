# Verifies: REQ-SG-021 (send_digest sends exactly one email when there are
#   events and none when there are none), REQ-SG-022 (STARTTLS and implicit
#   TLS/SSL are both supported, selected by config, not hardcoded).
# Scenario: drive SmtpNotifier against a fake SMTP client double recording
#   the calls it would make to a real smtplib.SMTP(_SSL) instance.

import smtplib

import pytest

from showgrab.adapters.notify import SmtpConfig, SmtpError, SmtpNotifier
from showgrab.core.models import NotifyEvent


class FakeSmtp:
    def __init__(self, fail_login=False, fail_send=False):
        self.ehlo_calls = 0
        self.starttls_called = False
        self.login_calls: list[tuple[str, str]] = []
        self.sent: list = []
        self.quit_called = False
        self._fail_login = fail_login
        self._fail_send = fail_send

    def ehlo(self):
        self.ehlo_calls += 1

    def starttls(self):
        self.starttls_called = True

    def login(self, user, password):
        if self._fail_login:
            raise smtplib.SMTPAuthenticationError(535, b"bad creds")
        self.login_calls.append((user, password))

    def send_message(self, msg):
        if self._fail_send:
            raise smtplib.SMTPException("boom")
        self.sent.append(msg)

    def quit(self):
        self.quit_called = True


def make_notifier(fake, use_tls=True, use_ssl=False) -> SmtpNotifier:
    config = SmtpConfig(
        host="smtp.gmail.com",
        port=587,
        username="andy@valerio.nu",
        password="app-password",
        from_addr="andy@valerio.nu",
        to_addrs=["andy@valerio.nu"],
        use_tls=use_tls,
        use_ssl=use_ssl,
    )
    return SmtpNotifier(config, client_factory=lambda: fake)


def test_send_digest_with_events_sends_one_email_via_starttls():
    fake = FakeSmtp()
    notifier = make_notifier(fake)
    events = [NotifyEvent("grabbed", ("1", 1, 1), "Silo", "S01E01 720p")]
    sent = notifier.send_digest(events)
    assert sent is True
    assert fake.starttls_called
    assert len(fake.sent) == 1
    assert fake.sent[0]["Subject"].startswith("showgrab digest")
    assert fake.login_calls == [("andy@valerio.nu", "app-password")]
    assert fake.quit_called


def test_send_digest_with_no_events_sends_nothing():
    fake = FakeSmtp()
    notifier = make_notifier(fake)
    sent = notifier.send_digest([])
    assert sent is False
    assert fake.sent == []


def test_implicit_ssl_skips_starttls():
    fake = FakeSmtp()
    notifier = make_notifier(fake, use_tls=False, use_ssl=True)
    notifier.send_digest([NotifyEvent("grabbed", ("1", 1, 1), "Silo", "x")])
    assert fake.starttls_called is False


def test_send_failure_raises_smtp_error():
    fake = FakeSmtp(fail_send=True)
    notifier = make_notifier(fake)
    with pytest.raises(SmtpError):
        notifier.send_digest([NotifyEvent("grabbed", ("1", 1, 1), "Silo", "x")])


def test_login_failure_raises_smtp_error():
    fake = FakeSmtp(fail_login=True)
    notifier = make_notifier(fake)
    with pytest.raises(SmtpError):
        notifier.send_digest([NotifyEvent("grabbed", ("1", 1, 1), "Silo", "x")])


def test_connection_ok():
    fake = FakeSmtp()
    notifier = make_notifier(fake)
    assert notifier.test_connection() is True


def test_dry_run_flag_reaches_the_subject_line():
    fake = FakeSmtp()
    notifier = make_notifier(fake)
    notifier.send_digest([NotifyEvent("grabbed", ("1", 1, 1), "Silo", "x")], dry_run=True)
    assert fake.sent[0]["Subject"].startswith("[DRY RUN]")
