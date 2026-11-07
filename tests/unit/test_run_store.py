import threading
from dataclasses import FrozenInstanceError

import pytest

from app.schemas.report import RunReport
from app.schemas.run import RunStatus
from app.schemas.target import RunTarget
from app.services.run_store import InMemoryRunStore, RunLimitError
from tests.unit.support import FakeClock

TARGET = RunTarget(site_url="https://example.com/")


def make(ttl=3600.0, max_runs=50, clock=None):
    counter = iter(range(10_000))
    clock = clock or FakeClock()
    store = InMemoryRunStore(
        ttl_seconds=ttl,
        max_runs=max_runs,
        clock=clock,
        id_factory=lambda: f"run{next(counter):020d}",
    )
    return store, clock


def test_created_run_is_running_and_can_be_read_back():
    store, clock = make()
    clock.now = 100.0
    record = store.create(TARGET)
    assert record.status is RunStatus.RUNNING
    assert record.target == TARGET and record.report is None and record.error is None
    assert record.created_at == 100.0 and record.finished_at is None
    assert store.get(record.run_id) == record


def test_unknown_run_is_none():
    store, _ = make()
    assert store.get("nothing") is None


def test_finish_stores_the_report_and_completes_the_run():
    store, clock = make()
    record = store.create(TARGET)
    clock.now = 5.0
    report = RunReport(results=[])
    store.finish(record.run_id, report)
    done = store.get(record.run_id)
    assert done.status is RunStatus.COMPLETED and done.report is report and done.finished_at == 5.0


def test_fail_stores_a_short_reason_and_marks_the_run_failed():
    store, _ = make()
    record = store.create(TARGET)
    store.fail(record.run_id, "Внутренняя ошибка приложения")
    failed = store.get(record.run_id)
    assert failed.status is RunStatus.FAILED and failed.error == "Внутренняя ошибка приложения"
    assert failed.report is None


def test_discard_removes_a_run_that_never_started():
    store, _ = make()
    record = store.create(TARGET)
    store.discard(record.run_id)
    assert store.get(record.run_id) is None
    store.discard(record.run_id)  # повторное удаление не ошибка


def test_finishing_an_unknown_run_is_an_error():
    store, _ = make()
    with pytest.raises(KeyError):
        store.finish("nothing", RunReport(results=[]))


def test_returned_records_are_immutable_snapshots():
    store, _ = make()
    record = store.create(TARGET)
    with pytest.raises(FrozenInstanceError):
        record.status = RunStatus.COMPLETED  # type: ignore[misc]
    store.finish(record.run_id, RunReport(results=[]))
    assert record.status is RunStatus.RUNNING  # прежний снимок не изменился


# Срок хранения и лимиты


def test_run_expires_after_the_time_to_live():
    store, clock = make(ttl=3600)
    record = store.create(TARGET)
    clock.now = 3600.0
    assert store.get(record.run_id) is not None
    clock.now = 3601.0
    assert store.get(record.run_id) is None


def test_oldest_finished_run_is_evicted_when_the_limit_is_reached():
    store, clock = make(max_runs=2)
    first = store.create(TARGET)
    store.finish(first.run_id, RunReport(results=[]))
    clock.now = 1.0
    second = store.create(TARGET)
    store.finish(second.run_id, RunReport(results=[]))
    clock.now = 2.0
    third = store.create(TARGET)
    assert store.get(first.run_id) is None
    assert store.get(second.run_id) is not None and store.get(third.run_id) is not None


def test_running_runs_are_never_evicted_and_overflow_is_refused():
    store, clock = make(max_runs=2)
    store.create(TARGET)
    clock.now = 1.0
    store.create(TARGET)
    with pytest.raises(RunLimitError):
        store.create(TARGET)


def test_finished_run_makes_room_for_a_new_one():
    store, _ = make(max_runs=1)
    record = store.create(TARGET)
    with pytest.raises(RunLimitError):
        store.create(TARGET)
    store.finish(record.run_id, RunReport(results=[]))
    assert store.create(TARGET) is not None


def test_default_ids_are_unguessable_and_url_safe():
    store = InMemoryRunStore()
    ids = {store.create(TARGET).run_id for _ in range(30)}
    assert len(ids) == 30
    assert all(len(run_id) >= 20 for run_id in ids)
    assert all(char.isalnum() or char in "-_" for run_id in ids for char in run_id)


def test_store_is_safe_under_concurrent_use():
    store, _ = make(max_runs=1000)
    created = []

    def worker():
        for _ in range(25):
            record = store.create(TARGET)
            store.finish(record.run_id, RunReport(results=[]))
            created.append(record.run_id)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(set(created)) == 200
    assert all(store.get(run_id).status is RunStatus.COMPLETED for run_id in created)


# Прогресс, остановка, лимит одновременных запусков


def partial(count):
    from app.schemas.result import CheckResult, Severity

    results = [
        CheckResult(
            check_id=f"QC-{n + 1:03d}",
            title="Проверка",
            status=__import__("app.schemas.status", fromlist=["CheckStatus"]).CheckStatus.PASSED,
            severity=Severity.LOW,
            expected="-",
            actual="-",
        )
        for n in range(count)
    ]
    return RunReport(results=results)


def test_progress_updates_partial_report_and_total_while_the_run_is_running():
    store, _ = make()
    record = store.create(TARGET)
    updated = store.update_progress(record.run_id, partial(3), total=25)
    assert updated.status is RunStatus.RUNNING
    assert len(updated.report.results) == 3 and updated.total == 25
    assert store.get(record.run_id) == updated


def test_late_progress_after_the_run_ended_is_ignored():
    store, _ = make()
    record = store.create(TARGET)
    store.stop(record.run_id, partial(2))
    store.update_progress(record.run_id, partial(9), total=25)
    assert len(store.get(record.run_id).report.results) == 2  # остановленный запуск не меняется


def test_request_stop_marks_only_running_runs():
    store, _ = make()
    running = store.create(TARGET)
    assert store.request_stop(running.run_id) is True
    assert store.get(running.run_id).stop_requested is True
    store.finish(running.run_id, partial(1))
    assert store.request_stop(running.run_id) is False
    assert store.request_stop("nothing") is False


def test_stop_stores_the_partial_report_and_marks_the_run_stopped():
    store, clock = make()
    record = store.create(TARGET)
    clock.now = 7.0
    stopped = store.stop(record.run_id, partial(4))
    assert stopped.status is RunStatus.STOPPED and stopped.finished_at == 7.0
    assert len(stopped.report.results) == 4


def test_max_running_limits_simultaneous_runs_but_not_finished_ones():
    store = InMemoryRunStore(max_runs=50, max_running=2)
    first = store.create(TARGET)
    store.create(TARGET)
    with pytest.raises(RunLimitError):
        store.create(TARGET)
    store.finish(first.run_id, partial(1))
    assert store.create(TARGET) is not None  # место освободилось
