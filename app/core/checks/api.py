import json

from app.core.checks.context import CheckContext
from app.core.checks.reporting import without_query
from app.core.openapi import check_required, load_spec, response_shape
from app.core.ports import HttpResponse
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

NO_BASE_REASON = "Базовый адрес API не указан"
NO_PATHS_REASON = "Пути для GET-запросов не указаны"
ALLOWED_STATUSES = (200,)


def _display_path(path: str) -> str:
    return path.split("?", 1)[0].split("#", 1)[0]


def _request_line(response: HttpResponse) -> str:
    return f"GET {without_query(response.url)} -> {response.status_code}"


def _is_json(response: HttpResponse) -> bool:
    return "json" in response.headers.get("content-type", "").lower()


def _is_success(response: HttpResponse) -> bool:
    return 200 <= response.status_code < 300


def _build(
    check: "_ApiCheck",
    status: CheckStatus,
    actual: str,
    *,
    reason: str | None = None,
    evidence: list[str] | None = None,
    duration_ms: int = 0,
) -> CheckResult:
    return CheckResult(
        check_id=check.check_id,
        title=check.title,
        status=status,
        severity=check.severity,
        expected=check.expected,
        actual=actual,
        reason=reason,
        evidence=evidence or [],
        duration_ms=duration_ms,
    )


class _ApiCheck:
    check_id: str
    title: str
    severity: Severity
    expected: str

    def _skipped(self, reason: str) -> CheckResult:
        return _build(self, CheckStatus.SKIPPED, "Не оценивалось", reason=reason)


class _UserPathsCheck(_ApiCheck):
    """Основа для проверок по GET-путям пользователя: условия и общие запросы."""

    def _skip_reason(self, context: CheckContext) -> str | None:
        if context.api_base_url is None:
            return NO_BASE_REASON
        if not context.api_paths:
            return NO_PATHS_REASON
        return None

    def _responses(self, context: CheckContext) -> list[tuple[str, HttpResponse]]:
        # api_get кэширует ответы: три проверки делают по одному запросу на путь
        return [(path, context.api_get(context.api_url(path))) for path in context.api_paths]


class ApiBaseResponds(_ApiCheck):
    check_id = "QC-020"
    title = "Базовый адрес API отвечает"
    severity = Severity.HIGH
    expected = "Сервер отвечает в пределах тайм-аута без ошибки 5xx"

    def run(self, context: CheckContext) -> CheckResult:
        if context.api_base_url is None:
            return self._skipped(NO_BASE_REASON)
        response = context.api_root()  # сетевой сбой превратит в BLOCKED исполнитель
        evidence = [_request_line(response)]
        if response.status_code >= 500:
            return _build(
                self,
                CheckStatus.FAILED,
                f"Сервер ответил ошибкой {response.status_code}",
                evidence=evidence,
                duration_ms=response.elapsed_ms,
            )
        return _build(
            self,
            CheckStatus.PASSED,
            f"Ответ {response.status_code} получен за {response.elapsed_ms} мс",
            evidence=evidence,
            duration_ms=response.elapsed_ms,
        )


class ApiStatusIsExpected(_UserPathsCheck):
    check_id = "QC-021"
    title = "Безопасный GET-endpoint возвращает ожидаемый статус"
    severity = Severity.HIGH
    expected = "Код ответа из ожидаемых для каждого указанного пути (по умолчанию 200)"

    def run(self, context: CheckContext) -> CheckResult:
        reason = self._skip_reason(context)
        if reason:
            return self._skipped(reason)
        responses = self._responses(context)
        evidence = [_request_line(response) for _, response in responses]
        wrong = []
        for path, response in responses:
            allowed = context.api_expected_statuses.get(path, ALLOWED_STATUSES)
            if response.status_code not in allowed:
                wanted = ", ".join(str(code) for code in allowed)
                wrong.append(
                    f"{_display_path(path)} -> {response.status_code} (ожидалось {wanted})"
                )
        if wrong:
            return _build(
                self,
                CheckStatus.FAILED,
                "Неожиданный статус: " + "; ".join(wrong),
                evidence=evidence,
            )
        return _build(
            self,
            CheckStatus.PASSED,
            f"Все пути ({len(responses)}) вернули ожидаемый статус",
            evidence=evidence,
        )


class ApiContentTypeMatches(_UserPathsCheck):
    check_id = "QC-022"
    title = "Content-Type соответствует формату"
    severity = Severity.MEDIUM
    expected = "У успешных ответов Content-Type содержит json"

    def run(self, context: CheckContext) -> CheckResult:
        reason = self._skip_reason(context)
        if reason:
            return self._skipped(reason)
        successful = [(p, r) for p, r in self._responses(context) if _is_success(r)]
        if not successful:
            return self._skipped("Нет успешных ответов, оценивать нечего")
        evidence = [_request_line(response) for _, response in successful]
        wrong = [
            f"{_display_path(path)} -> {response.headers.get('content-type') or 'заголовка нет'}"
            for path, response in successful
            if not _is_json(response)
        ]
        if wrong:
            return _build(
                self,
                CheckStatus.WARNING,
                "Не JSON: " + "; ".join(wrong),
                reason="Схему документации в MVP не читаем, ожидается application/json",
                evidence=evidence,
            )
        return _build(
            self,
            CheckStatus.PASSED,
            f"У всех успешных ответов ({len(successful)}) Content-Type: JSON",
            evidence=evidence,
        )


class ApiJsonParses(_UserPathsCheck):
    check_id = "QC-023"
    title = "JSON корректно разбирается"
    severity = Severity.HIGH
    expected = "Тело успешных JSON-ответов разбирается без ошибки"

    def run(self, context: CheckContext) -> CheckResult:
        reason = self._skip_reason(context)
        if reason:
            return self._skipped(reason)
        candidates = [(p, r) for p, r in self._responses(context) if _is_success(r) and _is_json(r)]
        if not candidates:
            return self._skipped("Нет успешных ответов, объявленных как JSON")
        evidence = [_request_line(response) for _, response in candidates]
        broken: list[str] = []
        truncated: list[str] = []
        for path, response in candidates:
            if response.truncated:
                truncated.append(_display_path(path))
            elif not self._parses(response):
                broken.append(_display_path(path))
        if broken:
            return _build(
                self,
                CheckStatus.FAILED,
                "JSON не разбирается: " + ", ".join(broken),
                evidence=evidence,
            )
        if truncated:
            return _build(
                self,
                CheckStatus.WARNING,
                "Ответ обрезан: " + ", ".join(truncated),
                reason="Тело больше лимита чтения, разбор невозможен",
                evidence=evidence,
            )
        return _build(
            self,
            CheckStatus.PASSED,
            f"JSON разобран у всех ответов ({len(candidates)})",
            evidence=evidence,
        )

    @staticmethod
    def _parses(response: HttpResponse) -> bool:
        try:
            json.loads(response.body.decode("utf-8", errors="replace"))
        except ValueError:
            return False
        return True


class ApiRequiredFields(_UserPathsCheck):
    check_id = "QC-024"
    title = "Обязательные поля присутствуют"
    severity = Severity.MEDIUM
    expected = "Обязательные поля из документации (OpenAPI/Swagger JSON) есть в ответе"

    def run(self, context: CheckContext) -> CheckResult:
        reason = self._skip_reason(context)
        if reason:
            return self._skipped(reason)
        if context.api_docs_url is None:
            return self._skipped("Адрес документации API не указан")
        docs = context.api_docs()
        if not _is_success(docs):
            return self._skipped(f"Документация недоступна: код {docs.status_code}")
        if docs.truncated:
            return self._skipped("Документация больше лимита чтения")
        spec = load_spec(docs.body)
        if spec is None:
            return self._skipped(
                "Документация не распознана как OpenAPI 3.x или Swagger 2.0 в формате JSON "
                "(YAML в MVP не читается)"
            )
        evaluated: list[str] = []
        problems: list[str] = []
        evidence: list[str] = []
        for path, response in self._responses(context):
            if not _is_success(response) or not _is_json(response) or response.truncated:
                continue
            shape = response_shape(spec, _display_path(path))
            if shape is None:
                continue
            try:
                data = json.loads(response.body.decode("utf-8", errors="replace"))
            except ValueError:
                continue  # неразбираемый JSON сообщает QC-023
            missing = check_required(data, shape)
            if missing is None:
                continue
            evaluated.append(path)
            evidence.append(_request_line(response))
            if missing:
                problems.append(f"{_display_path(path)}: нет {', '.join(missing)}")
        if not evaluated:
            return self._skipped(
                "Для указанных путей в документации нет схем ответа с обязательными полями"
            )
        if problems:
            return _build(
                self,
                CheckStatus.WARNING,
                "Расхождение с документацией: " + "; ".join(problems),
                reason="Схема читается упрощённо; сверьте ответ с документацией вручную",
                evidence=evidence,
            )
        return _build(
            self,
            CheckStatus.PASSED,
            f"Проверено путей: {len(evaluated)}, обязательные поля на месте",
            evidence=evidence,
        )


class UnknownPathIsHandled(_ApiCheck):
    check_id = "QC-025"
    title = "Неизвестный путь обрабатывается предсказуемо"
    severity = Severity.LOW
    expected = "Код 4xx и разбираемое тело, без ошибки 5xx"

    def run(self, context: CheckContext) -> CheckResult:
        if context.api_base_url is None:
            return self._skipped(NO_BASE_REASON)
        response = context.api_unknown_path()
        evidence = [_request_line(response)]
        status_code = response.status_code
        if status_code >= 500:
            return _build(
                self, CheckStatus.FAILED, f"Сервер ответил ошибкой {status_code}", evidence=evidence
            )
        if status_code < 400:
            return _build(
                self,
                CheckStatus.WARNING,
                f"Несуществующий путь вернул {status_code}",
                reason="Ошибки могут маскироваться под успешные ответы; ожидался код 4xx",
                evidence=evidence,
            )
        if _is_json(response) and not response.truncated and not self._parses(response):
            return _build(
                self,
                CheckStatus.WARNING,
                f"Код {status_code}, но тело ответа не является корректным JSON",
                reason="Заголовок говорит о JSON, а тело не разбирается",
                evidence=evidence,
            )
        return _build(self, CheckStatus.PASSED, f"Код {status_code}", evidence=evidence)

    @staticmethod
    def _parses(response: HttpResponse) -> bool:
        if not response.body:
            return True  # пустое тело допустимо для 4xx
        try:
            json.loads(response.body.decode("utf-8", errors="replace"))
        except ValueError:
            return False
        return True
