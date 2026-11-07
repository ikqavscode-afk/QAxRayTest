from dataclasses import dataclass
from html.parser import HTMLParser

_HIDDEN_TEXT_TAGS = frozenset({"script", "style", "noscript", "template", "title", "head"})


@dataclass(frozen=True)
class HtmlSummary:
    """Короткая сводка по исходному HTML (без выполнения скриптов)."""

    title: str | None
    lang: str | None
    has_html: bool
    has_head: bool
    has_body: bool
    visible_text: str
    script_count: int
    hrefs: tuple[str, ...]
    has_navigation: bool


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.has_html = False
        self.has_head = False
        self.has_body = False
        self.lang: str | None = None
        self.script_count = 0
        self.title_parts: list[str] = []
        self.text_parts: list[str] = []
        self._in_title = False
        self._title_done = False
        self._svg_depth = 0
        self._hidden_depth = 0
        self.hrefs: list[str] = []
        self.has_navigation = False
        self._header_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "html":
            self.has_html = True
            self.lang = next((v.strip() for k, v in attrs if k == "lang" and v and v.strip()), None)
        elif tag == "head":
            self.has_head = True
        elif tag == "body":
            self.has_body = True
            self._hidden_depth = 0  # начало body означает, что head закончился, даже без </head>
        elif tag == "script":
            self.script_count += 1
        elif tag == "svg":
            self._svg_depth += 1
        elif tag == "header":
            self._header_depth += 1
        elif tag == "nav":
            self.has_navigation = True
        elif tag == "a":
            self._handle_link(attrs)
        if any(k == "role" and v and v.strip().lower() == "navigation" for k, v in attrs):
            self.has_navigation = True
        if tag == "title" and not self._svg_depth and not self._title_done:
            self._in_title = True
        if tag in _HIDDEN_TEXT_TAGS:
            self._hidden_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag == "svg" and self._svg_depth:
            self._svg_depth -= 1
        if tag == "header" and self._header_depth:
            self._header_depth -= 1
        if tag == "title" and self._in_title:
            self._in_title = False
            self._title_done = True
        if tag in _HIDDEN_TEXT_TAGS and self._hidden_depth:
            self._hidden_depth -= 1

    def _handle_link(self, attrs: list[tuple[str, str | None]]) -> None:
        for key, value in attrs:
            if key == "href":
                self.hrefs.append((value or "").strip())
                if self._header_depth:
                    self.has_navigation = True  # блок ссылок в шапке считается навигацией
                return

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        elif not self._hidden_depth:
            self.text_parts.append(data)


def _collapse(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def summarize_html(body: bytes) -> HtmlSummary:
    """Разбирает байты ответа. Любой ввод допустим: сломанный HTML не вызывает ошибок."""
    collector = _Collector()
    collector.feed(body.decode("utf-8", errors="replace"))
    collector.close()
    return HtmlSummary(
        title=_collapse(collector.title_parts) or None,
        lang=collector.lang,
        has_html=collector.has_html,
        has_head=collector.has_head,
        has_body=collector.has_body,
        visible_text=_collapse(collector.text_parts),
        script_count=collector.script_count,
        hrefs=tuple(collector.hrefs),
        has_navigation=collector.has_navigation,
    )
