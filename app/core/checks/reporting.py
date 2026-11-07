from urllib.parse import urlsplit


def without_query(url: str) -> str:
    """Адрес без query и fragment: токены из параметров не должны попадать в отчёт."""
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}"
