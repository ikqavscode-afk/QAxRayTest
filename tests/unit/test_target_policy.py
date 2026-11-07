import pytest

from app.core.security.policy import TargetPolicy, TargetRejectedError

PUBLIC_IP = "93.184.216.34"


def resolver_returning(*ips):
    """Подделка DNS: любое имя «указывает» на заданные адреса."""
    return lambda host: list(ips)


def failing_resolver(host):
    raise OSError("имя не найдено")


@pytest.fixture
def policy():
    return TargetPolicy(resolver=resolver_returning(PUBLIC_IP))


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://example.com/path?q=1",
    ],
)
def test_http_and_https_are_allowed(policy, url):
    assert policy.validate(url) == url


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "gopher://example.com",
        "example.com",
        "",
    ],
)
def test_other_schemes_are_rejected(policy, url):
    with pytest.raises(TargetRejectedError):
        policy.validate(url)


def test_url_without_host_is_rejected(policy):
    with pytest.raises(TargetRejectedError):
        policy.validate("http://")


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1",
        "http://10.0.0.5:8080/admin",
        "http://192.168.1.1",
        "http://172.16.0.1",
        "http://169.254.169.254/latest/meta-data/",
        "http://100.64.0.1",
        "http://0.0.0.0",
        "http://224.0.0.1",
        "http://[::1]/",
        "http://[fe80::1]/",
        "http://[::ffff:127.0.0.1]/",
    ],
)
def test_internal_ip_literals_are_rejected(policy, url):
    with pytest.raises(TargetRejectedError):
        policy.validate(url)


def test_public_ip_literal_is_allowed(policy):
    assert policy.validate("http://93.184.216.34") == "http://93.184.216.34"


@pytest.mark.parametrize("ip", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_hostname_resolving_to_internal_ip_is_rejected(ip):
    policy = TargetPolicy(resolver=resolver_returning(ip))
    with pytest.raises(TargetRejectedError):
        policy.validate("http://innocent.example.com")


def test_hostname_with_any_internal_ip_is_rejected():
    policy = TargetPolicy(resolver=resolver_returning(PUBLIC_IP, "10.0.0.1"))
    with pytest.raises(TargetRejectedError):
        policy.validate("http://mixed.example.com")


def test_unresolvable_hostname_is_rejected():
    policy = TargetPolicy(resolver=failing_resolver)
    with pytest.raises(TargetRejectedError):
        policy.validate("http://no-such-host.example.com")


def test_rejection_error_names_the_field_and_defaults_to_site_url():
    assert TargetRejectedError("x").field == "site_url"
    assert TargetRejectedError("x", field="api_base_url").field == "api_base_url"
