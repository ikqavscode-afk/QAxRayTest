"""Проверяет репозиторий на запретные слова и личные данные.

Ищет в текстовых файлах названия брендов и учебных платформ, внутренние адреса,
упоминания инструментов-ассистентов, настоящие почты и телефоны.
Код возврата 1, если что-то найдено. Запуск: python scripts/check_content.py
"""

import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

RULES = [
    ("бренд", re.compile(r"яндекс|yandex|mesto|stellar\s*burgers", re.IGNORECASE)),
    ("учебная платформа", re.compile(r"учебн|практикум|praktikum|воркшоп|когорт", re.IGNORECASE)),
    ("внутренние адреса", re.compile(r"figma\.com|qa-faculty|education-services", re.IGNORECASE)),
    ("упоминание ассистента", re.compile(r"cl[a]ude|anth[r]opic", re.IGNORECASE)),
    ("телефон", re.compile(r"\+7\d{10}|\b8\d{10}\b")),
    (
        # GitHub блокирует пуш с такими строками, даже если они выдуманы: в тестах их собирают из частей
        "токеноподобная строка",
        re.compile(
            r"xox[baprs]-[A-Za-z0-9-]{10,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}"
            r"|AIza[0-9A-Za-z_-]{30,}|(?<![A-Za-z0-9])sk-[A-Za-z0-9]{20,}"
            r"|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}"
            r"|-----BEGIN [A-Z ]*PRIVATE KEY-----"
        ),
    ),
]
EMAIL = re.compile(r"[\w.+-]+@([\w-]+\.[\w.-]+)")
SAFE_DOMAINS = {"example.com", "example.org", "users.noreply.github.com"}
SKIP_DIRS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".mypy_cache",
    ".ruff_cache",
    ".pytest_cache",
}
SKIP_FILES = {"check_content.py"}  # содержит сами шаблоны поиска
TEXT_EXT = {
    ".md",
    ".html",
    ".json",
    ".py",
    ".yml",
    ".yaml",
    ".js",
    ".css",
    ".txt",
    ".toml",
    ".cfg",
    ".ini",
}


def scan_text(label: str, text: str, found: list[tuple[str, int, str, str]]) -> None:
    for number, line in enumerate(text.splitlines(), 1):
        for name, rx in RULES:
            for match in rx.finditer(line):
                found.append((label, number, name, match.group(0)))
        for match in EMAIL.finditer(line):
            if match.group(1).lower().rstrip(".") not in SAFE_DOMAINS:
                found.append((label, number, "почта", match.group(0)))


def scan_names(base: str, names: list[str], found: list[tuple[str, int, str, str]]) -> None:
    """Имена файлов и папок тоже проверяются: в них часто попадают ФИО и бренды."""
    for name in names:
        scan_text(os.path.relpath(os.path.join(base, name), ROOT), name, found)


def main() -> int:
    found: list[tuple[str, int, str, str]] = []
    for base, dirs, files in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        scan_names(base, dirs, found)
        for file_name in files:
            if file_name in SKIP_FILES:
                continue
            path = os.path.join(base, file_name)
            scan_names(base, [file_name], found)
            if os.path.splitext(file_name)[1].lower() in TEXT_EXT:
                text = Path(path).read_text(encoding="utf8", errors="ignore")
                scan_text(os.path.relpath(path, ROOT), text, found)
    for label, number, name, word in found:
        print(f"{label}:{number}: [{name}] {word}")
    print(f"Проверка контента: найдено {len(found)}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
