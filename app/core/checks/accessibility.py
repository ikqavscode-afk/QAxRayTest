from app.core.browser import DESKTOP
from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

MAX_BUTTONS = 50
LISTED_BUTTONS = 5
MAX_FIELDS = 50
LISTED_FIELDS = 5
REAL_LABELS = frozenset({"label-for", "label-wrap", "aria-label", "aria-labelledby"})


class ButtonsHaveNames:
    check_id = "QC-014"
    title = "У кнопок есть доступные имена"
    severity = Severity.MEDIUM
    expected = "У видимых кнопок есть текст, aria-label, aria-labelledby, alt, value или title"

    def run(self, context: CheckContext) -> CheckResult:
        snapshot = context.browser_page(DESKTOP).buttons(MAX_BUTTONS)
        if snapshot.total == 0:
            return self._skipped("На странице нет видимых кнопок")
        shown = len(snapshot.items)
        notes = []
        if snapshot.total > shown:
            notes.append(f"первые {shown} из {snapshot.total}")
        unnamed = [item for item in snapshot.items if item.name_source is None]
        if unnamed:
            names = [item.descriptor for item in unnamed[:LISTED_BUTTONS]]
            more = len(unnamed) - len(names)
            listing = ", ".join(names) + (f" и ещё {more}" if more > 0 else "")
            suffix = f" ({'; '.join(notes)})" if notes else ""
            return self._result(
                CheckStatus.WARNING,
                f"Без доступного имени {len(unnamed)} из {shown}: {listing}{suffix}",
                reason="У кнопки нет текста, aria-label или title: программа чтения с экрана "
                "не сможет её озвучить; проверьте вручную",
            )
        title_only = sum(1 for item in snapshot.items if item.name_source == "title")
        if title_only:
            notes.append(f"имя только из title: {title_only}")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        return self._result(CheckStatus.PASSED, f"Проверено кнопок: {shown}{suffix}")

    def _skipped(self, reason: str) -> CheckResult:
        return self._result(CheckStatus.SKIPPED, "Не оценивалось", reason=reason)

    def _result(
        self, status: CheckStatus, actual: str, *, reason: str | None = None
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected=self.expected,
            actual=actual,
            reason=reason,
        )


class FieldsHaveLabels:
    check_id = "QC-015"
    title = "У полей форм есть метки"
    severity = Severity.MEDIUM
    expected = "У каждого видимого поля есть label, aria-label или aria-labelledby (до 50 полей)"

    def run(self, context: CheckContext) -> CheckResult:
        snapshot = context.browser_page(DESKTOP).form_fields(MAX_FIELDS)
        if snapshot.total == 0:
            return self._skipped("На странице нет видимых полей форм")
        shown = len(snapshot.items)
        problems = [item for item in snapshot.items if item.name_source not in REAL_LABELS]
        note = f" (первые {shown} из {snapshot.total})" if snapshot.total > shown else ""
        if problems:
            parts = [
                item.descriptor
                if item.name_source is None
                else f"{item.descriptor} (только {item.name_source})"
                for item in problems[:LISTED_FIELDS]
            ]
            more = len(problems) - len(parts)
            listing = ", ".join(parts) + (f" и ещё {more}" if more > 0 else "")
            return self._result(
                CheckStatus.WARNING,
                f"Без подходящей метки {len(problems)} из {shown}: {listing}{note}",
                reason="Поле без label, aria-label или aria-labelledby не озвучивается программой "
                "чтения с экрана как нужно; placeholder и title заменяют метку неполностью; "
                "проверьте вручную",
            )
        return self._result(CheckStatus.PASSED, f"Проверено полей: {shown}{note}")

    def _skipped(self, reason: str) -> CheckResult:
        return self._result(CheckStatus.SKIPPED, "Не оценивалось", reason=reason)

    def _result(
        self, status: CheckStatus, actual: str, *, reason: str | None = None
    ) -> CheckResult:
        return CheckResult(
            check_id=self.check_id,
            title=self.title,
            status=status,
            severity=self.severity,
            expected=self.expected,
            actual=actual,
            reason=reason,
        )
