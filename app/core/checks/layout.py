from app.core.browser import DESKTOP, MOBILE
from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

TOLERANCE_PX = 1  # дробные размеры округляются, один пиксель не считаем переполнением
LISTED_CULPRITS = 3


class NoHorizontalOverflow:
    check_id = "QC-010"
    title = "Нет горизонтального переполнения"
    severity = Severity.LOW
    expected = "Содержимое не шире окна (допуск 1 px) при ширине 1280 и 375 px"

    def run(self, context: CheckContext) -> CheckResult:
        problems: list[str] = []
        for viewport in (DESKTOP, MOBILE):
            info = context.browser_page(viewport).overflow()
            excess = info.scroll_width - info.viewport_width
            if excess > TOLERANCE_PX:
                culprits = ", ".join(info.offenders[:LISTED_CULPRITS])
                suffix = f" ({culprits})" if culprits else ""
                problems.append(f"{viewport.width} px: шире окна на {excess} px{suffix}")
        if problems:
            return self._result(
                CheckStatus.WARNING,
                "; ".join(problems),
                reason="Возможна горизонтальная прокрутка; проверьте страницу вручную "
                "на этой ширине окна",
            )
        return self._result(CheckStatus.PASSED, "Переполнения нет при ширине 1280 и 375 px")

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
