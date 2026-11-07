"""Загрузка целей для живых тестов из JSON-файла, который лежит вне репозитория."""

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget, describe_errors

MAX_CASES = 50
_CHECK_ID = re.compile(r"^QC-\d{3}$")
_TARGET_KEYS = {"site_url", "api_docs_url", "api_base_url", "api_paths", "api_expected_statuses"}
_CASE_KEYS = _TARGET_KEYS | {"name", "expect", "rejected"}


class LiveTargetsError(ValueError):
    """Файл целей прочитать или разобрать нельзя; сообщение объясняет, что исправить."""


@dataclass(frozen=True)
class LiveCase:
    name: str
    target: RunTarget
    expect: dict[str, CheckStatus]
    rejected: bool = False


def load_cases(path: Path) -> list[LiveCase]:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise LiveTargetsError(f"Файл целей не прочитан: {path}") from error
    try:
        data = json.loads(raw)
    except ValueError as error:
        raise LiveTargetsError(f"Файл {path.name} не является корректным JSON") from error
    if not isinstance(data, list):
        raise LiveTargetsError("Файл целей должен содержать список объектов")
    if not data:
        raise LiveTargetsError("Список целей пуст")
    if len(data) > MAX_CASES:
        raise LiveTargetsError(f"Не более {MAX_CASES} целей в одном файле")
    cases = [_parse_case(item, number) for number, item in enumerate(data, start=1)]
    names = [case.name for case in cases]
    for name in dict.fromkeys(names):
        if names.count(name) > 1:
            raise LiveTargetsError(f"Имя цели «{name}» повторяется")
    return cases


def _parse_case(item: Any, number: int) -> LiveCase:
    if not isinstance(item, dict):
        raise LiveTargetsError(f"Цель №{number}: ожидался объект")
    name = item.get("name")
    if not isinstance(name, str) or not name.strip():
        raise LiveTargetsError(f"Цель №{number}: нет поля name")
    label = f"Цель «{name}»"
    unknown = sorted(set(item) - _CASE_KEYS)
    if unknown:
        raise LiveTargetsError(f"{label}: неизвестные поля {', '.join(unknown)}")
    if "site_url" not in item:
        raise LiveTargetsError(f"{label}: нет поля site_url")
    try:
        target = RunTarget(**{key: item[key] for key in _TARGET_KEYS if key in item})
    except ValidationError as error:
        details = "; ".join(f"{field}: {text}" for field, text in describe_errors(error).items())
        raise LiveTargetsError(f"{label}: {details}") from error
    return LiveCase(
        name=name,
        target=target,
        expect=_parse_expect(item.get("expect", {}), label),
        rejected=bool(item.get("rejected", False)),
    )


def _parse_expect(raw: Any, label: str) -> dict[str, CheckStatus]:
    if not isinstance(raw, dict):
        raise LiveTargetsError(f"{label}: expect должен быть объектом «QC-001»: «PASSED»")
    expect: dict[str, CheckStatus] = {}
    for check_id, status in raw.items():
        if not _CHECK_ID.match(str(check_id)):
            raise LiveTargetsError(f"{label}: некорректный идентификатор проверки «{check_id}»")
        try:
            expect[check_id] = CheckStatus(status)
        except ValueError as error:
            raise LiveTargetsError(f"{label}: неизвестный статус «{status}»") from error
    return expect
