from app.core.checks.availability import (
    ContentTypeIsHtml,
    HomepageResponds,
    HomepageStatusIsSuccessful,
    HttpsIsUsed,
    NoRedirectLoop,
)
from app.core.checks.base import Check


def default_checks() -> list[Check]:
    """Стандартный профиль. Новая проверка добавляется сюда одной строкой."""
    return [
        HomepageResponds(),
        HomepageStatusIsSuccessful(),
        HttpsIsUsed(),
        NoRedirectLoop(),
        ContentTypeIsHtml(),
    ]
