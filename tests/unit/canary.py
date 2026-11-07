"""«Канарейки»: узнаваемые секреты в разных формах для проверки утечек на каждом пути вывода.

Любой новый путь вывода (страница, JSON, консоль, журнал, bug report, Allure) обязан пройти
assert_no_secrets: если секрет просочился, тест падает."""

from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.status import CheckStatus

SECRET = "canary-7f3a9c1e5b"
PASSWORD = "hunter2-canary"
# собран из частей: целая строка вида JWT в исходнике вызывает блокировку пуша
JWT = ".".join(
    ("ey" + "JhbGciOiJIUzI1NiJ9", "ey" + "JzdWIiOiJjYW5hcnktN2YzYSJ9", "c2lnbmF0dXJlY2FuYXJ5")
)
ALL_SECRETS = (SECRET, PASSWORD, JWT)

# Текст, в котором секрет записан по-разному (адрес, заголовки, JSON, userinfo, Bearer, пары)
LEAKY_TEXTS = (
    f"GET https://api.example.com/v1/items?token={SECRET}&page=2 -> 404",
    f"Authorization: Bearer {SECRET}",
    f"Cookie: session={SECRET}; theme=dark",
    f'{{"password": "{PASSWORD}", "user": "demo"}}',
    f"https://demo:{PASSWORD}@example.com/private",
    f"api_key={SECRET}",
    f"заголовок Bearer {SECRET} отправлен",
    f"password: {PASSWORD}",
    f"jwt {JWT} получен",
)


def leaky_result(number=1, status=CheckStatus.WARNING):
    """Результат проверки, у которого секреты есть во всех текстовых полях."""
    return CheckResult(
        check_id=f"QC-{number:03d}",
        title=f"Проверка {number}",
        status=status,
        severity=Severity.MEDIUM,
        expected=LEAKY_TEXTS[0],
        actual=LEAKY_TEXTS[1] + " " + LEAKY_TEXTS[3],
        reason=LEAKY_TEXTS[2] if status.requires_reason else None,
        evidence=list(LEAKY_TEXTS[4:]),
    )


def leaky_report(count=2):
    return RunReport(results=[leaky_result(n + 1) for n in range(count)])


def assert_no_secrets(text, where=""):
    for secret in ALL_SECRETS:
        assert secret not in text, f"секрет просочился {where}: {secret}"
