import logging
import threading

import pytest

from app.core.security.policy import TargetRejectedError
from app.schemas.report import RunReport
from app.schemas.result import CheckResult, Severity
from app.schemas.run import RunStatus
from app.schemas.status import CheckStatus
from app.schemas.target import RunTarget
from app.services.run_manager import INTERNAL_ERROR, RunManager
from app.services.run_store import InMemoryRunStore, RunLimitError

TARGET = RunTarget(site_url="https://example.com/")


def result(number):
    return CheckResult(
        check_id=f"QC-{number:03d}",
        title="Проверка",
        status=CheckStatus.PASSED,
        severity=Severity.LOW,
        expected="-",
        actual="-",
    )


class ScriptedService:
    """Служба-заглушка: имитирует проверки; on_check позволяет вмешаться посреди запуска."""

    def __init__(self, total=3, error=None, reject=None, on_check=None):
        self.total = total
        self.error = error
        self.reject = reject
        self.on_check = on_check
        self.validated = []
        self.runs = 0

    def validate(self, target):
        self.validated.append(target)
        if self.reject:
            raise self.reject

    def run(self, target, *, on_progress=None, control=None):
        self.runs += 1
        results = []
        if on_progress:
            on_progress(RunReport(results=[]), self.total)
        for number in range(1, self.total + 1):
            if control is not None and control.should_stop():
                break
            if self.on_check:
                self.on_check(number)
            if self.error and number == 2:
                raise self.error
            results.append(result(number))
            if on_progress:
                on_progress(RunReport(results=list(results)), self.total)
        return RunReport(results=results)


def sync(job):
    job()


def make(service=None, store=None, starter=sync):
    store = store or InMemoryRunStore()
    service = service or ScriptedService()
    return RunManager(service, store, starter=starter), store, service


def test_address_is_validated_before_anything_is_created_or_started():
    manager, store, service = make(ScriptedService(reject=TargetRejectedError("внутренний адрес")))
    with pytest.raises(TargetRejectedError):
        manager.start(TARGET)
    assert store._runs == {} and service.runs == 0


def test_completed_run_stores_the_full_report_and_total():
    manager, store, _ = make()
    record = manager.start(TARGET)
    done = store.get(record.run_id)
    assert done.status is RunStatus.COMPLETED
    assert len(done.report.results) == 3 and done.total == 3


def test_progress_is_visible_in_the_store_while_the_run_is_going():
    snapshots = []
    store = InMemoryRunStore()

    def on_check(number):
        only = next(iter(store._runs.values()))
        snapshots.append((only.status, only.total, len(only.report.results)))

    manager, _, _ = make(ScriptedService(on_check=on_check), store)
    manager.start(TARGET)
    assert snapshots == [
        (RunStatus.RUNNING, 3, 0),
        (RunStatus.RUNNING, 3, 1),
        (RunStatus.RUNNING, 3, 2),
    ]


def test_unexpected_error_marks_the_run_failed_without_leaking_details(caplog):
    manager, store, _ = make(ScriptedService(error=RuntimeError("секретная подробность")))
    with caplog.at_level(logging.ERROR):
        record = manager.start(TARGET)
    failed = store.get(record.run_id)
    assert failed.status is RunStatus.FAILED and failed.error == INTERNAL_ERROR
    assert "секретная подробность" not in failed.error
    assert "секретная подробность" in caplog.text  # подробности только в журнале


def test_stop_during_the_run_gives_stopped_with_the_partial_report():
    store = InMemoryRunStore()
    holder = {}

    def on_check(number):
        if number == 2:  # запрос остановки посреди запуска
            assert holder["manager"].stop(next(iter(store._runs))) is True

    manager = RunManager(ScriptedService(total=5, on_check=on_check), store, starter=sync)
    holder["manager"] = manager
    manager.start(TARGET)
    stopped = next(iter(store._runs.values()))
    assert stopped.status is RunStatus.STOPPED and stopped.stop_requested is True
    assert [r.check_id for r in stopped.report.results] == ["QC-001", "QC-002"]


def test_stop_after_the_last_check_still_completes_the_run():
    store = InMemoryRunStore()
    holder = {}

    def on_check(number):
        if number == 3:  # остановка пришла, когда все проверки уже выполнены
            holder["manager"].stop(next(iter(store._runs)))

    manager = RunManager(ScriptedService(total=3, on_check=on_check), store, starter=sync)
    holder["manager"] = manager
    manager.start(TARGET)
    only = next(iter(store._runs.values()))
    assert only.status is RunStatus.COMPLETED and len(only.report.results) == 3


def test_stop_of_unknown_or_finished_run_returns_false():
    manager, _, _ = make()
    record = manager.start(TARGET)
    assert manager.stop(record.run_id) is False
    assert manager.stop("nothing") is False


def test_simultaneous_runs_are_limited():
    store = InMemoryRunStore(max_running=1)
    manager, _, _ = make(store=store, starter=lambda job: None)  # поток не стартует: запуск «идёт»
    manager.start(TARGET)
    with pytest.raises(RunLimitError):
        manager.start(TARGET)


def test_controls_are_released_when_runs_end():
    manager, _, _ = make()
    manager.start(TARGET)
    manager.start(TARGET)
    assert manager._controls == {}


# Настоящие потоки


def test_background_run_returns_immediately_and_stop_ends_it_quickly():
    release = threading.Event()
    started = threading.Event()

    class Slow(ScriptedService):
        def run(self, target, *, on_progress=None, control=None):
            if on_progress:
                on_progress(RunReport(results=[]), 4)
            started.set()
            while not control.should_stop() and not release.wait(0.01):
                pass
            return RunReport(results=[result(1)])

    store = InMemoryRunStore()
    manager = RunManager(Slow(), store)
    record = manager.start(TARGET)
    assert store.get(record.run_id).status is RunStatus.RUNNING  # start не ждёт конца
    assert started.wait(5)
    assert manager.stop(record.run_id) is True
    assert manager.wait(record.run_id, timeout=5) is True
    stopped = store.get(record.run_id)
    assert stopped.status is RunStatus.STOPPED and len(stopped.report.results) == 1
    release.set()


def test_background_threads_are_daemons_so_they_never_block_exit():
    manager = RunManager(ScriptedService(), InMemoryRunStore())
    record = manager.start(TARGET)
    manager.wait(record.run_id, timeout=5)
    assert all(thread.daemon for thread in manager._threads.values()) or not manager._threads
