import pytest

from inventory_aggregator.notifications.send_email import EmailSender, LoggingEmailSender


def test_email_sender_is_abstract() -> None:
    with pytest.raises(TypeError):
        EmailSender()  # type: ignore[abstract]


def test_logging_email_sender_logs_and_makes_no_network_call(caplog: pytest.LogCaptureFixture) -> None:
    sender = LoggingEmailSender()
    with caplog.at_level("INFO"):
        sender.send("subject line", "<p>body</p>", to="merchant@example.com")

    assert any("would send email" in record.message for record in caplog.records)
    assert any("merchant@example.com" in record.message for record in caplog.records)


def test_logging_email_sender_works_without_a_recipient(caplog: pytest.LogCaptureFixture) -> None:
    sender = LoggingEmailSender()
    with caplog.at_level("INFO"):
        sender.send("subject", "body")

    assert any("would send email" in record.message for record in caplog.records)
