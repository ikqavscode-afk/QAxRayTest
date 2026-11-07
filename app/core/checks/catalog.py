from app.core.checks.api import (
    ApiBaseResponds,
    ApiContentTypeMatches,
    ApiJsonParses,
    ApiStatusIsExpected,
    UnknownPathIsHandled,
)
from app.core.checks.availability import (
    ContentTypeIsHtml,
    HomepageResponds,
    HomepageStatusIsSuccessful,
    HttpsIsUsed,
    NoRedirectLoop,
)
from app.core.checks.base import Check
from app.core.checks.links import LinksAreWellFormed, NavigationFound
from app.core.checks.structure import BodyIsNotEmpty, HtmlStructure, PageHasTitle


def default_checks() -> list[Check]:
    """Стандартный профиль. Новая проверка добавляется сюда одной строкой."""
    return [
        HomepageResponds(),
        HomepageStatusIsSuccessful(),
        HttpsIsUsed(),
        NoRedirectLoop(),
        ContentTypeIsHtml(),
        PageHasTitle(),
        BodyIsNotEmpty(),
        HtmlStructure(),
        LinksAreWellFormed(),
        NavigationFound(),
        ApiBaseResponds(),
        ApiStatusIsExpected(),
        ApiContentTypeMatches(),
        ApiJsonParses(),
        UnknownPathIsHandled(),
    ]
