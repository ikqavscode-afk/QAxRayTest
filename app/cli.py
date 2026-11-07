import argparse
from collections.abc import Sequence
from typing import Protocol

from app.bootstrap import build_run_service
from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.status import CheckStatus


class Service(Protocol):
    def run(self, site_url: str) -> RunReport: ...


def format_report(report: RunReport) -> str:
    """Текст отчёта для консоли."""
    lines: list[str] = []
    for result in report.results:
        lines.append(f"{result.check_id}  {result.status.value:<8} {result.title}")
        if result.reason:
            lines.append(f"          причина: {result.reason}")
        if result.status is CheckStatus.FAILED:
            lines.append(f"          ожидалось: {result.expected}; факт: {result.actual}")
    lines.append("")
    lines.append("Итого: " + ", ".join(f"{s.value}: {n}" for s, n in report.summary.items()))
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None, service: Service | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qaxraytest", description="Базовые проверки сайта")
    parser.add_argument("url", help="адрес сайта, например https://example.com")
    args = parser.parse_args(argv)
    runner = service or build_run_service()
    try:
        report = runner.run(args.url)
    except TargetRejectedError as error:
        print(f"Адрес отклонён: {error}")
        return 2
    print(format_report(report))
    problems = report.summary[CheckStatus.FAILED] + report.summary[CheckStatus.BLOCKED]
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
