"""Вход и регистрация: только структура страницы. Данные не вводятся и не отправляются."""

from app.core.browser import DESKTOP, AuthForm, AuthSnapshot
from app.core.checks.accessibility import REAL_LABELS
from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

# Образцы поиска в тексте, aria-label, title и адресе ссылки (регистр не важен)
LOGIN_PATTERN = r"(вход|войти|войдите|авторизац|log\s?in|sign\s?in|signin|/login)"
SIGNUP_PATTERN = (
    r"(регистрац|зарегистр|sign\s?up|signup|register|create\s+(an\s+)?account"
    r"|создать\s+(аккаунт|учётн))"
)
LISTED_FORMS = 3
LISTED_PROBLEMS = 5


def _snapshot(context: CheckContext) -> AuthSnapshot:
    """Проба выполняется один раз на запуск и общая для QC-017, QC-018, QC-019."""
    return context.cached(
        "auth", lambda: context.browser_page(DESKTOP).auth_signals(LOGIN_PATTERN, SIGNUP_PATTERN)
    )


def _is_signup(form: AuthForm) -> bool:
    """Форма регистрации: два пароля, пароль нового пользователя или признак в атрибутах формы."""
    return form.new_password or form.password_count >= 2 or form.signup_hint


def _result(
    check: "_AuthCheck", status: CheckStatus, actual: str, *, reason: str | None = None
) -> CheckResult:
    return CheckResult(
        check_id=check.check_id,
        title=check.title,
        status=status,
        severity=check.severity,
        expected=check.expected,
        actual=actual,
        reason=reason,
    )


class _AuthCheck:
    check_id: str
    title: str
    severity: Severity
    expected: str

    def _skipped(self, reason: str) -> CheckResult:
        return _result(self, CheckStatus.SKIPPED, "Не оценивалось", reason=reason)


class LoginIsFound(_AuthCheck):
    check_id = "QC-017"
    title = "Найдена ссылка или форма входа"
    severity = Severity.LOW
    expected = "На странице есть ссылка входа или поле пароля"

    def run(self, context: CheckContext) -> CheckResult:
        snapshot = _snapshot(context)
        if not snapshot.login_links and not snapshot.forms:
            return self._skipped(
                "Ссылка или форма входа не найдены (возможно, они появляются после действия "
                "пользователя)"
            )
        return _result(
            self,
            CheckStatus.PASSED,
            f"Ссылок входа: {len(snapshot.login_links)}; форм с полем пароля: {len(snapshot.forms)}",
        )


class LoginFormStructure(_AuthCheck):
    check_id = "QC-018"
    title = "Структура формы входа"
    severity = Severity.MEDIUM
    expected = "В форме входа есть поле логина, поле пароля, кнопка отправки и метки у полей"

    def run(self, context: CheckContext) -> CheckResult:
        forms = [form for form in _snapshot(context).forms if not _is_signup(form)]
        if not forms:
            return self._skipped("На странице нет формы входа с полем пароля")
        judged = forms[:LISTED_FORMS]
        problems = [(form, self._problems(form)) for form in judged]
        broken = [f"{form.descriptor}: {'; '.join(items)}" for form, items in problems if items]
        if broken:
            return _result(
                self,
                CheckStatus.WARNING,
                "; ".join(broken),
                reason="Проверялась только структура формы, данные не вводились; "
                "результат эвристический, проверьте форму вручную",
            )
        names = ", ".join(form.descriptor for form in judged)
        return _result(
            self,
            CheckStatus.PASSED,
            f"Проверено форм входа: {len(judged)} ({names}): есть логин, пароль, "
            "кнопка отправки и метки",
        )

    @staticmethod
    def _problems(form: AuthForm) -> list[str]:
        problems: list[str] = []
        usernames = [
            field
            for field in form.fields
            if field.kind.startswith("input:") and field.kind != "input:password"
        ]
        if not usernames:
            problems.append("нет поля логина")
        if form.submit_count == 0:
            problems.append("нет кнопки отправки")
        unlabeled = []
        for field in form.fields:
            if field.name_source in REAL_LABELS:
                continue
            weak = f" (только {field.name_source})" if field.name_source else ""
            unlabeled.append(f"{field.descriptor}{weak}")
        if unlabeled:
            shown = ", ".join(unlabeled[:LISTED_PROBLEMS])
            problems.append(f"нет метки у {shown}")
        return problems


class SignupIsFound(_AuthCheck):
    check_id = "QC-019"
    title = "Найдена форма или ссылка регистрации"
    severity = Severity.LOW
    expected = "На странице есть ссылка или форма регистрации"

    def run(self, context: CheckContext) -> CheckResult:
        snapshot = _snapshot(context)
        forms = [form for form in snapshot.forms if _is_signup(form)]
        if not snapshot.signup_links and not forms:
            return self._skipped(
                "Ссылка или форма регистрации не найдены (возможно, они появляются после "
                "действия пользователя)"
            )
        return _result(
            self,
            CheckStatus.PASSED,
            f"Ссылок регистрации: {len(snapshot.signup_links)}; форм регистрации: {len(forms)}",
        )
