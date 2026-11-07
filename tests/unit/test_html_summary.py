from app.core.html_summary import summarize_html


def html_bytes(text):
    return text.encode("utf-8")


def test_title_is_extracted_and_trimmed():
    html = "<html><head><title>  Мой \n сайт </title></head><body>x</body></html>"
    assert summarize_html(html_bytes(html)).title == "Мой сайт"


def test_missing_title_is_none():
    assert summarize_html(b"<html><head></head><body>x</body></html>").title is None


def test_empty_title_is_none():
    assert (
        summarize_html(b"<html><head><title>   </title></head><body>x</body></html>").title is None
    )


def test_svg_title_is_not_page_title():
    html = "<html><head></head><body><svg><title>Иконка</title></svg></body></html>"
    assert summarize_html(html_bytes(html)).title is None


def test_structure_flags_and_lang():
    summary = summarize_html(b'<!DOCTYPE html><html lang="ru"><head></head><body>x</body></html>')
    assert summary.has_html and summary.has_head and summary.has_body
    assert summary.lang == "ru"


def test_missing_lang_is_none():
    assert summarize_html(b"<html><head></head><body>x</body></html>").lang is None


def test_visible_text_ignores_scripts_styles_and_title():
    html = (
        "<html><head><title>Заголовок</title><style>p{color:red}</style></head><body>"
        "<script>var a = 'много текста в скрипте';</script><p>Привет</p></body></html>"
    )
    summary = summarize_html(html_bytes(html))
    assert summary.visible_text == "Привет"
    assert summary.script_count == 1


def test_noscript_hint_does_not_count_as_visible_text():
    html = "<html><body><noscript>Включите JavaScript</noscript><div id='root'></div></body></html>"
    assert summarize_html(html_bytes(html)).visible_text == ""


def test_empty_spa_shell_has_no_text_but_has_scripts():
    html = b'<html><head></head><body><div id="root"></div><script src="/app.js"></script></body></html>'
    summary = summarize_html(html)
    assert summary.visible_text == ""
    assert summary.script_count == 1


def test_text_whitespace_is_collapsed():
    html = "<html><body><p>один</p>\n\n   <p>два</p></body></html>"
    assert summarize_html(html_bytes(html)).visible_text == "один два"


def test_adjacent_elements_are_separated_by_space():
    html = "<html><body><p>один</p><p>два</p></body></html>"
    assert summarize_html(html_bytes(html)).visible_text == "один два"


def test_not_html_has_no_structure():
    summary = summarize_html(b'{"status": "ok"}')
    assert not summary.has_html
    assert not summary.has_body


def test_broken_html_does_not_crash():
    summary = summarize_html(html_bytes("<html><body><p>не закрыто <b>жирный"))
    assert "не закрыто" in summary.visible_text


def test_invalid_utf8_does_not_crash():
    summary = summarize_html(b"<html><body>\xff\xfe text</body></html>")
    assert "text" in summary.visible_text


def test_empty_body_bytes():
    summary = summarize_html(b"")
    assert summary.title is None
    assert summary.visible_text == ""
    assert not summary.has_html


def test_text_is_visible_when_head_end_tag_is_omitted():
    html = "<html><head><title>T</title><body><p>Текст страницы</p></body></html>"
    assert summarize_html(html_bytes(html)).visible_text == "Текст страницы"
