import pytest

from app.core.browser import (
    DESKTOP,
    AuthForm,
    AuthSnapshot,
    BrowserUnavailableError,
    ControlInfo,
)
from app.core.browser_session import BrowserSession
from app.core.checks.auth import (
    LOGIN_PATTERN,
    SIGNUP_PATTERN,
    LoginFormStructure,
    LoginIsFound,
    SignupIsFound,
)
from app.core.checks.context import CheckContext
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp


def field(descriptor, source="label-for", kind="input:text"):
    return ControlInfo(kind=kind, descriptor=descriptor, name_source=source)


def login_fields():
    return (
        field("input#user", kind="input:email"),
        field("input#pass", kind="input:password"),
    )


def form(
    descriptor="form#login",
    passwords=1,
    new_password=False,
    signup_hint=False,
    submit=1,
    fields=None,
):
    return AuthForm(
        descriptor=descriptor,
        password_count=passwords,
        new_password=new_password,
        signup_hint=signup_hint,
        submit_count=submit,
        fields=login_fields() if fields is None else fields,
    )


def snapshot(login_links=(), signup_links=(), forms=()):
    return AuthSnapshot(
        login_links=tuple(login_links), signup_links=tuple(signup_links), forms=tuple(forms)
    )


def context_for(auth):
    browser = FakeBrowser()
    browser.auth_snapshot = auth
    session = BrowserSession(FakeFactory(browser), SITE)
    return CheckContext(http=FakeHttp(), site_url=SITE, browser=session), browser


def run(check, auth):
    context, browser = context_for(auth)
    return check.run(context), browser


# Общее: образцы поиска передаются пробе, проба выполняется один раз на запуск


def test_patterns_are_passed_to_the_probe_and_it_runs_once_for_all_three_checks():
    context, browser = context_for(snapshot(login_links=["a#login"]))
    for check in (LoginIsFound(), LoginFormStructure(), SignupIsFound()):
        check.run(context)
    assert browser.auth_calls == [(LOGIN_PATTERN, SIGNUP_PATTERN)]
    assert browser.opens[0][1] == DESKTOP


def test_patterns_know_russian_and_english_words():
    import re

    login, signup = (
        re.compile(LOGIN_PATTERN, re.IGNORECASE),
        re.compile(SIGNUP_PATTERN, re.IGNORECASE),
    )
    for text in ["Войти", "Вход", "Log in", "Sign in", "/login", "Авторизация"]:
        assert login.search(text), text
    for text in ["Регистрация", "Зарегистрироваться", "Sign up", "Register", "Create an account"]:
        assert signup.search(text), text
    assert not login.search("Каталог") and not signup.search("Контакты")


# QC-017: найдена ссылка или форма входа


def test_qc017_passes_with_a_login_link():
    result, _ = run(LoginIsFound(), snapshot(login_links=["a#login", "a.nav"]))
    assert result.check_id == "QC-017"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW
    assert "2" in result.actual


def test_qc017_passes_with_a_password_field_even_without_a_link():
    result, _ = run(LoginIsFound(), snapshot(forms=[form()]))
    assert result.status is CheckStatus.PASSED


def test_qc017_is_skipped_when_nothing_found():
    result, _ = run(LoginIsFound(), snapshot())
    assert result.status is CheckStatus.SKIPPED
    assert "вход" in result.reason.lower()


# QC-018: структура формы входа


def test_qc018_is_skipped_without_a_login_form():
    result, _ = run(LoginFormStructure(), snapshot(login_links=["a#login"]))
    assert result.check_id == "QC-018"
    assert result.status is CheckStatus.SKIPPED
    assert "форм" in result.reason


def test_qc018_passes_for_a_complete_form():
    result, _ = run(LoginFormStructure(), snapshot(forms=[form()]))
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM
    assert "form#login" in result.actual


@pytest.mark.parametrize(
    ("changes", "fragment"),
    [
        ({"submit": 0}, "кнопк"),
        ({"fields": (field("input#pass", kind="input:password"),)}, "логина"),
        (
            {"fields": (field("input#user", None), field("input#pass", kind="input:password"))},
            "input#user",
        ),
        (
            {"fields": (field("input#user"), field("input#pass", "placeholder", "input:password"))},
            "placeholder",
        ),
    ],
)
def test_qc018_warns_about_what_is_missing(changes, fragment):
    result, _ = run(LoginFormStructure(), snapshot(forms=[form(**changes)]))
    assert result.status is CheckStatus.WARNING
    assert fragment in result.actual
    assert result.reason


@pytest.mark.parametrize(
    "signup_form",
    [
        form("form#reg", passwords=2),
        form("form#reg", new_password=True),
        form("form#reg", signup_hint=True),
    ],
)
def test_signup_forms_are_not_judged_as_login_forms(signup_form):
    result, _ = run(LoginFormStructure(), snapshot(forms=[signup_form]))
    assert result.status is CheckStatus.SKIPPED


def test_qc018_judges_only_login_forms_when_both_exist():
    forms = [form("form#reg", passwords=2, submit=0), form("form#login")]
    result, _ = run(LoginFormStructure(), snapshot(forms=forms))
    assert result.status is CheckStatus.PASSED


def test_qc018_never_fails_it_is_a_heuristic():
    bad = form(submit=0, fields=(field("input#p", None, "input:password"),))
    result, _ = run(LoginFormStructure(), snapshot(forms=[bad]))
    assert result.status is not CheckStatus.FAILED


# QC-019: найдена форма или ссылка регистрации


def test_qc019_passes_with_a_signup_link():
    result, _ = run(SignupIsFound(), snapshot(signup_links=["a#join"]))
    assert result.check_id == "QC-019"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.LOW


@pytest.mark.parametrize(
    "signup_form",
    [form("form#reg", passwords=2), form("form#reg", new_password=True), form(signup_hint=True)],
)
def test_qc019_passes_with_a_signup_form(signup_form):
    result, _ = run(SignupIsFound(), snapshot(forms=[signup_form]))
    assert result.status is CheckStatus.PASSED


def test_qc019_is_skipped_when_nothing_found():
    result, _ = run(SignupIsFound(), snapshot(forms=[form()]))  # только форма входа
    assert result.status is CheckStatus.SKIPPED
    assert "регистрац" in result.reason


@pytest.mark.parametrize("check", [LoginIsFound, LoginFormStructure, SignupIsFound])
def test_without_a_connected_browser_the_error_reaches_the_runner(check):
    with pytest.raises(BrowserUnavailableError):
        check().run(CheckContext(http=FakeHttp(), site_url=SITE))
