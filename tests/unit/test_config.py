import pytest
import requests
import responses
from pydantic import ValidationError

from app.bootstrap import build_http_client, build_run_service
from app.config import Settings, SettingsError
from app.core.ports import NetworkError, TooManyRedirectsError
from app.core.security.policy import TargetPolicy
from app.services.run_service import RunService

URL = "https://example.com/"


def policy():
    return TargetPolicy(resolver=lambda host: ["93.184.216.34"])


# Значения по умолчанию и чтение окружения


def test_defaults_match_the_values_used_before_configuration_existed():
    settings = Settings()
    assert settings.http_timeout == 10.0
    assert settings.max_bytes == 1_000_000
    assert settings.max_redirects == 5
    assert settings.max_retries == 2
    assert settings.retry_delay == 0.5
    assert settings.max_parallel_runs == 2
    assert settings.rate_limit_runs == 10
    assert settings.rate_limit_window == 60.0
    assert settings.cookie_secure is False
    assert settings.allowed_hosts == ("localhost", "127.0.0.1", "[::1]")
    assert settings.trusted_proxies == 0
    assert settings.max_run_seconds == 300.0


def test_empty_environment_gives_defaults():
    assert Settings.from_env({}) == Settings()


def test_unrelated_and_blank_variables_are_ignored():
    environment = {"PATH": "/usr/bin", "QAXRAY_HTTP_TIMEOUT": "   ", "QAXRAY_MAX_BYTES": ""}
    assert Settings.from_env(environment) == Settings()


def test_all_variables_are_read():
    settings = Settings.from_env(
        {
            "QAXRAY_HTTP_TIMEOUT": "7.5",
            "QAXRAY_MAX_BYTES": "250000",
            "QAXRAY_MAX_REDIRECTS": "3",
            "QAXRAY_MAX_RETRIES": "1",
            "QAXRAY_RETRY_DELAY": "1.5",
        }
    )
    assert settings == Settings(
        http_timeout=7.5, max_bytes=250_000, max_redirects=3, max_retries=1, retry_delay=1.5
    )


@pytest.mark.parametrize(
    "environment",
    [
        {"QAXRAY_HTTP_TIMEOUT": "1", "QAXRAY_MAX_BYTES": "10000", "QAXRAY_MAX_REDIRECTS": "0"},
        {"QAXRAY_HTTP_TIMEOUT": "30", "QAXRAY_MAX_BYTES": "5000000", "QAXRAY_MAX_REDIRECTS": "10"},
        {"QAXRAY_MAX_RETRIES": "0", "QAXRAY_RETRY_DELAY": "0"},
        {"QAXRAY_MAX_RETRIES": "3", "QAXRAY_RETRY_DELAY": "5"},
        {
            "QAXRAY_MAX_PARALLEL_RUNS": "1",
            "QAXRAY_RATE_LIMIT_RUNS": "1",
            "QAXRAY_RATE_LIMIT_WINDOW": "10",
        },
        {
            "QAXRAY_MAX_PARALLEL_RUNS": "4",
            "QAXRAY_RATE_LIMIT_RUNS": "30",
            "QAXRAY_RATE_LIMIT_WINDOW": "600",
        },
    ],
)
def test_boundary_values_are_accepted(environment):
    Settings.from_env(environment)


# Границы безопасности


@pytest.mark.parametrize(
    ("variable", "value"),
    [
        ("QAXRAY_HTTP_TIMEOUT", "0.5"),
        ("QAXRAY_HTTP_TIMEOUT", "31"),
        ("QAXRAY_MAX_BYTES", "9999"),
        ("QAXRAY_MAX_BYTES", "5000001"),
        ("QAXRAY_MAX_REDIRECTS", "-1"),
        ("QAXRAY_MAX_REDIRECTS", "11"),
        ("QAXRAY_MAX_RETRIES", "4"),
        ("QAXRAY_RETRY_DELAY", "-1"),
        ("QAXRAY_RETRY_DELAY", "6"),
        ("QAXRAY_MAX_PARALLEL_RUNS", "0"),
        ("QAXRAY_MAX_PARALLEL_RUNS", "5"),
        ("QAXRAY_RATE_LIMIT_RUNS", "0"),
        ("QAXRAY_RATE_LIMIT_RUNS", "31"),
        ("QAXRAY_RATE_LIMIT_WINDOW", "9"),
        ("QAXRAY_RATE_LIMIT_WINDOW", "601"),
        ("QAXRAY_TRUSTED_PROXIES", "-1"),
        ("QAXRAY_TRUSTED_PROXIES", "4"),
        ("QAXRAY_MAX_RUN_SECONDS", "29"),
        ("QAXRAY_MAX_RUN_SECONDS", "901"),
    ],
)
def test_out_of_range_values_are_rejected_and_name_the_variable(variable, value):
    with pytest.raises(SettingsError) as caught:
        Settings.from_env({variable: value})
    assert variable in str(caught.value)


@pytest.mark.parametrize("value", ["abc", "nan", "inf", "-inf", "1e999"])
def test_non_numeric_and_non_finite_values_are_rejected(value):
    with pytest.raises(SettingsError) as caught:
        Settings.from_env({"QAXRAY_HTTP_TIMEOUT": value})
    assert "QAXRAY_HTTP_TIMEOUT" in str(caught.value)


def test_fractional_value_for_integer_setting_is_rejected():
    with pytest.raises(SettingsError, match="QAXRAY_MAX_REDIRECTS"):
        Settings.from_env({"QAXRAY_MAX_REDIRECTS": "2.5"})


def test_all_problems_are_reported_together():
    with pytest.raises(SettingsError) as caught:
        Settings.from_env({"QAXRAY_HTTP_TIMEOUT": "0", "QAXRAY_MAX_RETRIES": "9"})
    message = str(caught.value)
    assert "QAXRAY_HTTP_TIMEOUT" in message and "QAXRAY_MAX_RETRIES" in message


def test_settings_cannot_be_built_out_of_range_directly_either():
    with pytest.raises(ValidationError):
        Settings(http_timeout=999)


def test_settings_are_immutable():
    with pytest.raises(ValidationError):
        Settings().http_timeout = 1.0  # type: ignore[misc]


def test_there_is_no_setting_that_disables_the_address_policy():
    assert not any("polic" in name or "ssrf" in name for name in Settings.model_fields)


# Настройки действительно доходят до HTTP-клиента


@responses.activate
def test_redirect_limit_comes_from_settings():
    responses.add(responses.GET, URL, status=302, headers={"Location": "/next"})
    client = build_http_client(Settings(max_redirects=0), policy())
    with pytest.raises(TooManyRedirectsError):
        client.get(URL)


@responses.activate
def test_retry_count_comes_from_settings():
    responses.add(responses.GET, URL, body=requests.ConnectionError("нет сети"))
    client = build_http_client(Settings(max_retries=1, retry_delay=0), policy())
    with pytest.raises(NetworkError):
        client.get(URL)
    assert len(responses.calls) == 2  # первая попытка и один повтор


@responses.activate
def test_body_limit_comes_from_settings():
    responses.add(responses.GET, URL, body=b"x" * 20_000)
    client = build_http_client(Settings(max_bytes=10_000), policy())
    response = client.get(URL)
    assert response.truncated and len(response.body) <= 10_000


@responses.activate
def test_timeout_comes_from_settings():
    responses.add(responses.GET, URL, body=b"ok")
    build_http_client(Settings(http_timeout=3.0), policy()).get(URL)
    assert responses.calls[0].request.req_kwargs["timeout"] == 3.0


def test_run_service_is_built_from_environment(monkeypatch):
    monkeypatch.setenv("QAXRAY_MAX_REDIRECTS", "2")
    assert isinstance(build_run_service(), RunService)


def test_bad_environment_stops_the_build_with_a_clear_error(monkeypatch):
    monkeypatch.setenv("QAXRAY_HTTP_TIMEOUT", "abc")
    with pytest.raises(SettingsError, match="QAXRAY_HTTP_TIMEOUT"):
        build_run_service()


# Настройки браузера


def test_browser_defaults():
    settings = Settings()
    assert settings.browser_page_timeout == 20.0
    assert settings.browser_headless is True


def test_browser_variables_are_read():
    settings = Settings.from_env(
        {"QAXRAY_BROWSER_PAGE_TIMEOUT": "30", "QAXRAY_BROWSER_HEADLESS": "0"}
    )
    assert settings.browser_page_timeout == 30.0
    assert settings.browser_headless is False
    assert Settings.from_env({"QAXRAY_BROWSER_HEADLESS": "1"}).browser_headless is True


@pytest.mark.parametrize("value", ["4.9", "60.1", "0", "abc", "nan"])
def test_browser_page_timeout_out_of_range_is_rejected(value):
    with pytest.raises(SettingsError, match="QAXRAY_BROWSER_PAGE_TIMEOUT"):
        Settings.from_env({"QAXRAY_BROWSER_PAGE_TIMEOUT": value})


@pytest.mark.parametrize("value", ["5", "60"])
def test_browser_page_timeout_boundaries_are_accepted(value):
    Settings.from_env({"QAXRAY_BROWSER_PAGE_TIMEOUT": value})


@pytest.mark.parametrize("value", ["yes", "true", "2", "-1"])
def test_browser_headless_accepts_only_zero_or_one(value):
    with pytest.raises(SettingsError, match="QAXRAY_BROWSER_HEADLESS.*0 или 1"):
        Settings.from_env({"QAXRAY_BROWSER_HEADLESS": value})


def test_browser_and_http_problems_are_reported_together():
    with pytest.raises(SettingsError) as caught:
        Settings.from_env({"QAXRAY_HTTP_TIMEOUT": "0", "QAXRAY_BROWSER_HEADLESS": "yes"})
    assert "QAXRAY_HTTP_TIMEOUT" in str(caught.value)
    assert "QAXRAY_BROWSER_HEADLESS" in str(caught.value)


def test_service_is_built_without_starting_a_browser(monkeypatch):
    def forbidden(options):
        raise AssertionError("браузер не должен запускаться при сборке сервиса")

    monkeypatch.setattr("app.adapters.browser.selenium_browser._chrome_driver", forbidden)
    assert isinstance(build_run_service(Settings()), RunService)


# Режим для одноразовых машин CI: Chrome без песочницы (по умолчанию выключен)


def test_browser_sandbox_is_enabled_by_default():
    assert Settings().browser_no_sandbox is False


def test_no_sandbox_variable_is_read_as_zero_or_one():
    assert Settings.from_env({"QAXRAY_BROWSER_NO_SANDBOX": "1"}).browser_no_sandbox is True
    assert Settings.from_env({"QAXRAY_BROWSER_NO_SANDBOX": "0"}).browser_no_sandbox is False
    assert Settings.from_env({"QAXRAY_BROWSER_NO_SANDBOX": " "}).browser_no_sandbox is False


@pytest.mark.parametrize("value", ["yes", "true", "2"])
def test_no_sandbox_accepts_only_zero_or_one(value):
    with pytest.raises(SettingsError, match="QAXRAY_BROWSER_NO_SANDBOX.*0 или 1"):
        Settings.from_env({"QAXRAY_BROWSER_NO_SANDBOX": value})


# Список разрешённых имён хоста


def test_allowed_hosts_are_read_normalised_and_trimmed():
    settings = Settings.from_env({"QAXRAY_ALLOWED_HOSTS": " Demo.Example.org , localhost "})
    assert settings.allowed_hosts == ("demo.example.org", "localhost")


@pytest.mark.parametrize(
    "value",
    [
        "*",
        "*.example.org",
        "evil.example/path",
        "a b",
        "http://x.example",
        "x" * 300,
        ",".join(f"h{n}.example" for n in range(11)),
    ],
)
def test_bad_allowed_hosts_are_rejected_and_name_the_variable(value):
    with pytest.raises(SettingsError, match="QAXRAY_ALLOWED_HOSTS"):
        Settings.from_env({"QAXRAY_ALLOWED_HOSTS": value})
