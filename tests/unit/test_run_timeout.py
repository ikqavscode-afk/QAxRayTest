import threading

from app.schemas.report import RunReport
from app.schemas.run import RunStatus
from app.schemas.target import RunTarget
from app.services.run_manager import RunManager
from app.services.run_store import InMemoryRunStore

TARGET = RunTarget(site_url="https://example.com/")


class Waiting:
    """Служба, которая ждёт остановки: имитирует запуск, который не укладывается в срок."""

    def __init__(self):
        self.stopped_by_control = threading.Event()

    def validate(self, target):
        return None

    def run(self, target, on_progress=None, control=None):
        while not control.should_stop():
            threading.Event().wait(0.01)
        self.stopped_by_control.set()
        return RunReport(results=[])


def test_run_is_stopped_when_the_time_limit_is_reached():
    service = Waiting()
    store = InMemoryRunStore()
    manager = RunManager(service, store, max_run_seconds=0.2)
    record = manager.start(TARGET)
    store.update_progress(record.run_id, RunReport(results=[]), 5)
    assert manager.wait(record.run_id, 5)
    assert service.stopped_by_control.is_set()
    assert store.get(record.run_id).status is RunStatus.STOPPED


def test_timer_is_cancelled_when_the_run_finishes_in_time():
    class Quick(Waiting):
        def run(self, target, on_progress=None, control=None):
            return RunReport(results=[])

    store = InMemoryRunStore()
    manager = RunManager(Quick(), store, max_run_seconds=30)
    record = manager.start(TARGET)
    assert manager.wait(record.run_id, 5)
    assert store.get(record.run_id).status is RunStatus.COMPLETED
    for timer in [t for t in threading.enumerate() if t.name.startswith("run-timeout")]:
        timer.join(1)  # отменённый таймер завершается не мгновенно
    assert not [t for t in threading.enumerate() if t.name.startswith("run-timeout")]


def test_no_limit_by_default_keeps_old_behaviour():
    class Quick(Waiting):
        def run(self, target, on_progress=None, control=None):
            return RunReport(results=[])

    store = InMemoryRunStore()
    manager = RunManager(Quick(), store)
    record = manager.start(TARGET)
    assert manager.wait(record.run_id, 5)
    assert store.get(record.run_id).status is RunStatus.COMPLETED
