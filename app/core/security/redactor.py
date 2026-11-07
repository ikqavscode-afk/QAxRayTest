"""Маскирование секретов в тексте, результатах и отчётах.

Применяется на границах: при сохранении результата, при выводе в консоль и на страницу, в журналах.
Правила намеренно предпочитают лишнее маскирование пропуску секрета. Ограничение: произвольные
«непрозрачные» строки без признаков секрета (случайные хеши, идентификаторы без имени параметра)
не распознаются."""

import re
from collections.abc import Callable
from re import Match

from app.schemas.report import RunReport
from app.schemas.result import CheckResult

MASK = "***"

# Имена параметров и полей, значения которых считаются секретами (без учёта регистра)
_NAMES = (
    r"(?:access[_-]?token|id[_-]?token|refresh[_-]?token|auth[_-]?token|x[_-]?api[_-]?key|api[_-]?key"
    r"|client[_-]?secret|session[_-]?id|token|key|secret|password|passwd|pwd|passphrase|session|sid"
    r"|authorization|auth|signature|sig|credentials?)"
)
_FLAGS = re.IGNORECASE


def _keep_prefix(*groups: str) -> Callable[[Match[str]], str]:
    """Оставляет перечисленные группы, всё остальное заменяет маской."""

    def replace(match: Match[str]) -> str:
        return "".join(match.group(name) for name in groups) + MASK

    return replace


def _userinfo(match: Match[str]) -> str:
    return f"{match.group('scheme')}{MASK}@"


# Порядок важен: от узких форматов к общим парам «имя: значение»
_RULES: tuple[tuple[re.Pattern[str], Callable[[Match[str]], str] | str], ...] = (
    # Заголовки: значение до конца строки
    (
        re.compile(
            r"^(?P<head>[ \t]*(?:proxy-authorization|authorization|set-cookie|cookie|x-api-key"
            r"|x-auth-token))(?P<sep>[ \t]*:[ \t]*)(?P<val>[^\r\n]*)$",
            _FLAGS | re.MULTILINE,
        ),
        _keep_prefix("head", "sep"),
    ),
    # JWT
    (
        re.compile(r"(?<![A-Za-z0-9_-])eyJ[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}\.[A-Za-z0-9_-]{5,}"),
        MASK,
    ),
    # Известные форматы ключей
    (re.compile(r"(?<![A-Za-z0-9])AKIA[0-9A-Z]{16}"), MASK),
    (re.compile(r"(?<![A-Za-z0-9])gh[pousr]_[A-Za-z0-9]{30,}"), MASK),
    (re.compile(r"(?<![A-Za-z0-9])xox[baprs]-[A-Za-z0-9-]{20,}"), MASK),
    (re.compile(r"(?<![A-Za-z0-9])AIza[0-9A-Za-z_-]{30,}"), MASK),
    (re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"), MASK),
    # Bearer и Basic
    (
        re.compile(r"\b(?P<scheme>Bearer|Basic)(?P<sep>\s+)[A-Za-z0-9._~+/=-]{8,}", _FLAGS),
        _keep_prefix("scheme", "sep"),
    ),
    # Логин и пароль внутри адреса: https://user:pass@host
    (
        re.compile(r"(?<![A-Za-z0-9+.-])(?P<scheme>[A-Za-z][A-Za-z0-9+.-]*://)[^/\s@\"'<>]+@"),
        _userinfo,
    ),
    # JSON и словари: "password": "значение" и "password": 123
    (
        re.compile(
            rf"(?P<k>[\"'])(?P<name>{_NAMES})(?P=k)(?P<sep>\s*[:=]\s*)(?P<q>[\"'])(?P<val>[^\"']*)(?P=q)",
            _FLAGS,
        ),
        lambda m: (
            f"{m.group('k')}{m.group('name')}{m.group('k')}{m.group('sep')}{m.group('q')}{MASK}{m.group('q')}"
        ),
    ),
    (
        re.compile(
            rf"(?P<k>[\"'])(?P<name>{_NAMES})(?P=k)(?P<sep>\s*:\s*)(?P<val>[^\s,}}\]\"']+)", _FLAGS
        ),
        lambda m: f"{m.group('k')}{m.group('name')}{m.group('k')}{m.group('sep')}{MASK}",
    ),
    # name="значение"
    (
        re.compile(
            rf"(?<![A-Za-z0-9])(?P<name>{_NAMES})(?P<sep>\s*[:=]\s*)(?P<q>[\"'])(?P<val>[^\"']*)(?P=q)",
            _FLAGS,
        ),
        lambda m: f"{m.group('name')}{m.group('sep')}{m.group('q')}{MASK}{m.group('q')}",
    ),
    # Параметры адреса и простые пары: token=abc, password: abc
    (
        re.compile(
            rf"(?<![A-Za-z0-9])(?P<name>{_NAMES})(?P<sep>\s*[:=]\s*)(?P<val>[^\s,;&\"'<>#]+)",
            _FLAGS,
        ),
        _keep_prefix("name", "sep"),
    ),
)


# Закрытые ключи: тело ограничено по длине, иначе повторяющиеся «BEGIN» без «END» дают квадратичное время
_KEY_BLOCK_MAX = 8192
_KEY_RULES: tuple[tuple[re.Pattern[str], str], ...] = (
    (
        re.compile(
            rf"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]{{0,{_KEY_BLOCK_MAX}}}?-----END [A-Z ]*PRIVATE KEY-----"
        ),
        MASK,
    ),
    (
        re.compile(rf"-----BEGIN [A-Z ]*PRIVATE KEY-----[A-Za-z0-9+/=\s]{{20,{_KEY_BLOCK_MAX}}}"),
        MASK,
    ),
)


class Redactor:
    def redact_text(self, text: str) -> str:
        if "PRIVATE KEY" in text:  # дорогие правила только там, где они могут сработать
            for key_pattern, key_mask in _KEY_RULES:
                text = key_pattern.sub(key_mask, text)
        for pattern, replacement in _RULES:
            text = pattern.sub(replacement, text)
        return text

    def redact_result(self, result: CheckResult) -> CheckResult:
        """Копия результата с замаскированными текстами; идентификатор, статус и важность прежние."""
        return result.model_copy(
            update={
                "expected": self.redact_text(result.expected),
                "actual": self.redact_text(result.actual),
                "reason": self.redact_text(result.reason) if result.reason else result.reason,
                "evidence": [self.redact_text(line) for line in result.evidence],
            }
        )

    def redact_report(self, report: RunReport) -> RunReport:
        return RunReport(results=[self.redact_result(result) for result in report.results])
