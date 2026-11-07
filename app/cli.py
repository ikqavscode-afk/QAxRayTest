import argparse
from collections.abc import Sequence
from typing import Protocol

from pydantic import ValidationError

from app.bootstrap import build_run_service
from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget, describe_errors, parse_expectation

FIELD_LABELS = {
    "site_url": "адрес сайта",
    "api_docs_url": "--api-docs",
    "api_base_url": "--api-base",
    "api_paths": "--api-path",
    "api_expected_statuses": "--api-expect",
}


class Service(Protocol):
    def run(self, target: RunTarget) -> RunReport: ...


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
    parser.add_argument("--api-docs", help="адрес документации API (необязательно)")
    parser.add_argument("--api-base", help="базовый адрес API (необязательно)")
    parser.add_argument(
        "--api-path",
        action="append",
        default=[],
        help="безопасный GET-путь API, например /health; можно указать несколько раз",
    )
    parser.add_argument(
        "--api-expect",
        action="append",
        default=[],
        help="ожидаемый статус для пути: /ping=201 или /items=200,404 (по умолчанию 200)",
    )
    args = parser.parse_args(argv)
    expected: dict[str, tuple[int, ...]] = {}
    try:
        for text in args.api_expect:
            path, statuses = parse_expectation(text)
            expected.setdefault(path, statuses)
    except ValueError as error:
        print(f"Параметры отклонены ({FIELD_LABELS['api_expected_statuses']}): {error}")
        return 2
    try:
        target = RunTarget(
            site_url=args.url,
            api_docs_url=args.api_docs,
            api_base_url=args.api_base,
            api_paths=tuple(args.api_path),
            api_expected_statuses=expected,
        )
    except ValidationError as error:
        for field, message in describe_errors(error).items():
            print(f"Параметры отклонены ({FIELD_LABELS[field]}): {message}")
        return 2
    runner = service or build_run_service()
    try:
        report = runner.run(target)
    except TargetRejectedError as error:
        print(f"Адрес отклонён: {error}")
        return 2
    print(format_report(report))
    problems = report.summary[CheckStatus.FAILED] + report.summary[CheckStatus.BLOCKED]
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
