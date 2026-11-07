from urllib.parse import urlsplit

from app.core.browser import DESKTOP, ImageInfo
from app.core.checks.context import CheckContext
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

MAX_IMAGES = 30
MAX_NAME = 60
LISTED_NAMES = 5


def _is_svg(src: str) -> bool:
    lowered = src.lower()
    return lowered.endswith(".svg") or lowered.startswith("data:image/svg")


def _loaded(image: ImageInfo) -> bool:
    # SVG без собственных размеров даёт naturalWidth 0, хотя нарисован
    return image.complete and (image.natural_width > 0 or _is_svg(image.src))


def _name(src: str) -> str:
    path = urlsplit(src).path if src.startswith("http") else src
    return (path or src)[-MAX_NAME:]


class ImagesLoad:
    check_id = "QC-009"
    title = "Изображения загружаются"
    severity = Severity.MEDIUM
    expected = "У видимых изображений (до 30) браузер загрузил содержимое"

    def run(self, context: CheckContext) -> CheckResult:
        snapshot = context.browser_page(DESKTOP).images(MAX_IMAGES)
        if snapshot.total == 0:
            return self._skipped("На странице нет видимых изображений")
        pending = [image for image in snapshot.items if image.lazy and not image.complete]
        judged = [image for image in snapshot.items if image not in pending]
        if not judged:
            return self._skipped("Все показанные изображения ленивые и ещё не загружены")
        broken = [image for image in judged if not _loaded(image)]
        if broken:
            names = [_name(image.src) for image in broken[:LISTED_NAMES]]
            more = len(broken) - len(names)
            listing = ", ".join(names) + (f" и ещё {more}" if more > 0 else "")
            return self._result(
                CheckStatus.WARNING,
                f"Не загрузились {len(broken)} из {len(judged)}: {listing}",
                reason="Изображение могло быть недоступно, заблокировано или заменено скриптом; "
                "проверьте страницу вручную",
            )
        notes = []
        if snapshot.total > len(snapshot.items):
            notes.append(f"первые {len(snapshot.items)} из {snapshot.total}")
        if pending:
            notes.append(f"ленивых, не оценено: {len(pending)}")
        suffix = f" ({'; '.join(notes)})" if notes else ""
        return self._result(CheckStatus.PASSED, f"Проверено изображений: {len(judged)}{suffix}")

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
