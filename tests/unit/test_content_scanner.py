"""Сканер контента не пропускает токеноподобные строки: GitHub блокирует такой пуш (даже для выдуманных значений)."""

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_content.py"
spec = importlib.util.spec_from_file_location("check_content", SCRIPT)
check_content = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_content)

# Значения собираются из частей: в исходнике нет ни одной целой токеноподобной строки
TOKEN_LIKE = {
    "slack": "xo" + "xb-" + "123456789012-1234567890123-abcdefghijklmnopqrstuvwx",
    "github": "gh" + "p_" + "a1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q7R8",
    "aws": "AK" + "IA" + "IOSFODNN7EXAMPLE",
    "google": "AI" + "za" + "SyA1B2c3D4e5F6g7H8i9J0k1L2m3N4o5P6q",
    "openai": "s" + "k-" + "a1B2c3D4e5F6g7H8i9J0k1L2m3",
    "jwt": "ey"
    + "Jhbc"
    + "GciOiJIUzI1NiJ9"
    + "."
    + "eyJzdWIiOiJ4eHh4eHh4"
    + "."
    + "c2lnbmF0dXJlYWJj",
    "private key": "-----" + "BEGIN RSA PRIVATE KEY" + "-----",
}


def scan(text):
    found = []
    check_content.scan_text("файл.py", text, found)
    return [name for _, _, name, _ in found]


@pytest.mark.parametrize("kind", list(TOKEN_LIKE))
def test_token_like_strings_are_reported(kind):
    assert "токеноподобная строка" in scan(f"секрет = '{TOKEN_LIKE[kind]}'")


@pytest.mark.parametrize(
    "text",
    [
        r"re.compile(r'xox[baprs]-[A-Za-z0-9-]{20,}')",  # шаблон поиска, а не токен
        "ключ api_key передаётся в заголовке",
        "task-force-planning-notes-for-the-team",  # «sk-» внутри слова
        "Проверено ссылок: 10, заглушек нет",
    ],
)
def test_normal_text_and_search_patterns_are_not_reported(text):
    assert "токеноподобная строка" not in scan(text)


def test_the_repository_itself_contains_no_token_like_strings(capsys):
    assert check_content.main() == 0, capsys.readouterr().out
