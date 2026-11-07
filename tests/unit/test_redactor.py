import time

import pytest

from app.core.security.redactor import MASK, Redactor
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus
from tests.unit.canary import ALL_SECRETS, JWT, LEAKY_TEXTS, PASSWORD, SECRET, leaky_report

REDACTOR = Redactor()
# Границы блока закрытого ключа собираются из частей (в исходнике нет целой строки с маркером)
BEGIN_RSA = "-----" + "BEGIN RSA PRIVATE KEY" + "-----"
END_RSA = "-----" + "END RSA PRIVATE KEY" + "-----"
BEGIN_PLAIN = "-----" + "BEGIN PRIVATE KEY" + "-----"


def clean(text):
    return REDACTOR.redact_text(text)


# Параметры адреса и формы


def test_sensitive_query_parameters_are_masked_and_others_are_kept():
    text = f"https://api.example.com/a?token={SECRET}&page=2&sort=asc"
    assert clean(text) == f"https://api.example.com/a?token={MASK}&page=2&sort=asc"


@pytest.mark.parametrize(
    "name",
    [
        "token", "access_token", "refresh_token", "id_token", "api_key", "apikey", "api-key",
        "key", "secret", "client_secret", "password", "passwd", "pwd", "session", "sessionid",
        "sid", "auth", "authorization", "signature", "sig", "credentials",
    ],
)  # fmt: skip
def test_every_sensitive_parameter_name_is_masked_case_insensitively(name):
    for variant in (name, name.upper(), name.title()):
        out = clean(f"https://x.example/?{variant}={SECRET}&ok=1")
        assert SECRET not in out and "ok=1" in out


def test_fragment_tokens_are_masked():
    assert SECRET not in clean(f"https://x.example/cb#access_token={SECRET}&state=1")


def test_plain_key_value_pairs_are_masked():
    for text in (f"password: {PASSWORD}", f"password = {PASSWORD}", f"api_key={SECRET}"):
        assert PASSWORD not in clean(text) and SECRET not in clean(text)


# Заголовки


@pytest.mark.parametrize(
    "line",
    [
        f"Authorization: Bearer {SECRET}",
        f"authorization: Basic {SECRET}",
        f"Proxy-Authorization: {SECRET}",
        f"Cookie: sid={SECRET}; theme=dark",
        f"Set-Cookie: sid={SECRET}; HttpOnly; Secure",
        f"X-Api-Key: {SECRET}",
        f"X-Auth-Token: {SECRET}",
    ],
)
def test_sensitive_headers_are_masked_to_the_end_of_the_line(line):
    out = clean(f"Server: nginx\n{line}\nAccept: */*")
    assert SECRET not in out
    assert "Server: nginx" in out and "Accept: */*" in out  # соседние строки целы


def test_content_type_and_other_harmless_headers_are_not_touched():
    for text in ("Content-Type: application/json", "Accept: text/html", "Cache-Control: no-cache"):
        assert clean(text) == text


# JSON, Python-словари, Bearer, JWT, адрес с логином


def test_json_and_dict_style_secrets_are_masked_but_other_fields_stay():
    out = clean(f'{{"password": "{PASSWORD}", "user": "demo"}}')
    assert PASSWORD not in out and '"user": "demo"' in out
    assert PASSWORD not in clean(f"{{'api_key': '{PASSWORD}', 'x': 1}}")


def test_bearer_and_basic_tokens_are_masked_anywhere_in_text():
    out = clean(f"отправлен заголовок Bearer {SECRET} на сервер")
    assert SECRET not in out and f"Bearer {MASK}" in out


def test_jwt_is_masked():
    assert JWT not in clean(f"получен {JWT} от сервера")


def test_credentials_inside_the_address_are_masked():
    out = clean(f"https://demo:{PASSWORD}@example.com/private")
    assert PASSWORD not in out and "demo" not in out and "@example.com/private" in out


@pytest.mark.parametrize(
    "secret",
    [
        "AK" + "IA" + "IOSFODNN7EXAMPLE",
        "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
        "xo" + "xb-" + "123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
        "AI" + "za" + "SyA1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q",
        "sk-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3",
    ],
)
def test_well_known_secret_formats_are_masked(secret):
    assert secret not in clean(f"найден ключ {secret} в ответе")


def test_private_key_blocks_are_masked_whole():
    block = f"{BEGIN_RSA}\nMIIEowIBAAKCAQEA\nabc\n{END_RSA}"
    out = clean(f"до\n{block}\nпосле")
    assert "MIIEowIBAAKCAQEA" not in out and "до" in out and "после" in out


# Обычный текст отчётов остаётся нетронутым


@pytest.mark.parametrize(
    "text",
    [
        "Проверено ссылок: 10, заглушек нет",
        "GET https://example.com/a -> 200",
        "Фокус дошёл только до 3 из 10 элементов",
        "Нет видимого индикатора фокуса у: input#user-name.input_error, input#password.input_error",
        "Без доступного имени 2 из 3: button#icon, a.btn",
        "Ответ получен за 120 мс",
        "Content-Type: text/html; charset=utf-8",
        "Заголовок: Магазин",
        "Не найдено: head, атрибут lang",
        "Ссылок входа: 1; форм с полем пароля: 1",
    ],
)
def test_normal_report_text_is_returned_unchanged(text):
    assert clean(text) == text


# Свойства


def test_redaction_is_idempotent():
    for text in LEAKY_TEXTS:
        once = clean(text)
        assert clean(once) == once


def test_every_canary_form_is_fully_masked():
    for text in LEAKY_TEXTS:
        out = clean(text)
        for secret in ALL_SECRETS:
            assert secret not in out, (text, out)


def test_empty_and_whitespace_text_is_safe():
    assert clean("") == "" and clean("   ") == "   "


def test_long_hostile_input_is_processed_quickly():
    hostile = ("token=" * 20_000) + ("a" * 200_000) + ("Authorization: " * 5_000)
    started = time.monotonic()
    clean(hostile)
    assert time.monotonic() - started < 2.0


# Результаты и отчёты


def test_result_text_fields_are_masked_and_identity_is_preserved():
    original = CheckResult(
        check_id="QC-012",
        title="Внутренние ссылки",
        status=CheckStatus.WARNING,
        severity=Severity.HIGH,
        expected=f"?token={SECRET}",
        actual=f"Authorization: Bearer {SECRET}",
        reason=f"api_key={SECRET}",
        evidence=[f"GET /a?password={PASSWORD} -> 404", "GET /b -> 200"],
        duration_ms=120,
    )
    safe = REDACTOR.redact_result(original)
    assert safe is not original and SECRET in original.actual  # исходный не изменён
    blob = " ".join([safe.expected, safe.actual, safe.reason or "", *safe.evidence])
    assert SECRET not in blob and PASSWORD not in blob
    assert (safe.check_id, safe.title, safe.status, safe.severity, safe.duration_ms) == (
        "QC-012", "Внутренние ссылки", CheckStatus.WARNING, Severity.HIGH, 120,
    )  # fmt: skip
    assert safe.evidence[1] == "GET /b -> 200"


def test_report_masks_every_result_and_keeps_order_and_summary():
    report = leaky_report(3)
    safe = REDACTOR.redact_report(report)
    assert [r.check_id for r in safe.results] == [r.check_id for r in report.results]
    assert safe.summary == report.summary
    assert not any(secret in safe.model_dump_json() for secret in ALL_SECRETS)


def test_empty_report_is_returned_as_an_equal_empty_report():
    assert REDACTOR.redact_report(RunReport(results=[])) == RunReport(results=[])


def test_repeated_private_key_markers_and_other_hostile_shapes_are_processed_quickly():
    shapes = (
        BEGIN_RSA * 8000,
        BEGIN_PLAIN + "AAAA\n" * 8000,
        "a" * 200_000,  # длинная строка букв (раньше правило «схема://» давало квадратичное время)
        "a://" * 40_000,
        '"password":"' * 20_000,
        "Bearer " * 30_000,
    )
    started = time.monotonic()
    for shape in shapes:
        clean(shape)
    assert time.monotonic() - started < 5.0
