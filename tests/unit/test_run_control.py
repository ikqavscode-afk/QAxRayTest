import threading

from app.services.run_control import RunControl


def test_control_starts_not_stopped():
    assert RunControl().should_stop() is False


def test_request_stop_sets_the_flag_and_is_idempotent():
    control = RunControl()
    control.request_stop()
    control.request_stop()
    assert control.should_stop() is True


def test_abort_callback_runs_once_when_stop_is_requested():
    calls = []
    control = RunControl()
    control.set_abort(lambda: calls.append(1))
    control.request_stop()
    control.request_stop()
    assert calls == [1]


def test_abort_registered_after_the_stop_runs_immediately():
    calls = []
    control = RunControl()
    control.request_stop()
    control.set_abort(lambda: calls.append(1))
    assert calls == [1]


def test_failing_abort_callback_does_not_break_the_stop():
    def broken():
        raise RuntimeError("браузер уже закрыт")

    control = RunControl()
    control.set_abort(broken)
    control.request_stop()  # не бросает
    assert control.should_stop() is True


def test_concurrent_stop_requests_run_the_abort_callback_once():
    calls = []
    control = RunControl()
    control.set_abort(lambda: calls.append(1))
    threads = [threading.Thread(target=control.request_stop) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert calls == [1]
