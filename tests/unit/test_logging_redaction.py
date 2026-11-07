import io
import logging

import pytest

from app.logging_setup import RedactingFormatter, configure_logging
from tests.unit.canary import PASSWORD, SECRET, assert_no_secrets


@pytest.fixture
def stream():
    root = logging.getLogger()
    saved = (root.handlers[:], root.level)
    buffer = io.StringIO()
    root.handlers.clear()
    configure_logging(stream=buffer)
    yield buffer
    root.handlers[:] = saved[0]
    root.setLevel(saved[1])


def test_secrets_in_log_messages_and_arguments_are_masked(stream):
    log = logging.getLogger("qaxray.test")
    log.warning("запрос https://x.example/?token=%s не удался", SECRET)
    log.warning(f"Authorization: Bearer {SECRET}")
    assert_no_secrets(stream.getvalue(), "в журнале")
    assert "не удался" in stream.getvalue()


def test_secrets_inside_exception_tracebacks_are_masked(stream):
    log = logging.getLogger("qaxray.test")
    try:
        raise RuntimeError(
            f"HTTPSConnectionPool: Max retries with url: /a?api_key={SECRET}&password={PASSWORD}"
        )
    except RuntimeError:
        log.exception("сбой запуска")
    text = stream.getvalue()
    assert "Traceback" in text and "RuntimeError" in text  # сам след сохранён
    assert_no_secrets(text, "в трассировке")


def test_normal_messages_are_left_as_they_are(stream):
    logging.getLogger("qaxray.test").warning("Запуск %s завершён за %s с", "abc", 12)
    assert "Запуск abc завершён за 12 с" in stream.getvalue()


def test_configure_logging_is_idempotent(stream):
    configure_logging(stream=stream)
    configure_logging(stream=stream)
    logging.getLogger("qaxray.test").warning("одно сообщение")
    assert stream.getvalue().count("одно сообщение") == 1


def test_formatter_masks_secrets_even_when_used_directly():
    record = logging.LogRecord("n", logging.INFO, "f", 1, f"token={SECRET}", None, None)
    assert SECRET not in RedactingFormatter("%(message)s").format(record)
