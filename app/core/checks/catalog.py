from app.core.checks.accessibility import (
    ButtonsHaveNames,
    FieldsHaveLabels,
    KeyboardNavigation,
)
from app.core.checks.api import (
    ApiBaseResponds,
    ApiContentTypeMatches,
    ApiJsonParses,
    ApiRequiredFields,
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
from app.core.checks.images import ImagesLoad
from app.core.checks.layout import NoHorizontalOverflow
from app.core.checks.links import InternalLinksAreReachable, LinksAreWellFormed, NavigationFound
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
        ImagesLoad(),
        NoHorizontalOverflow(),
        LinksAreWellFormed(),
        InternalLinksAreReachable(),
        NavigationFound(),
        ButtonsHaveNames(),
        FieldsHaveLabels(),
        KeyboardNavigation(),
        ApiBaseResponds(),
        ApiStatusIsExpected(),
        ApiContentTypeMatches(),
        ApiJsonParses(),
        ApiRequiredFields(),
        UnknownPathIsHandled(),
    ]
