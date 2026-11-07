from app.core.checks.context import CheckContext
from tests.unit.support import SITE, FakeHttp


def test_cached_runs_the_factory_once_per_key():
    context = CheckContext(http=FakeHttp(), site_url=SITE)
    calls = []

    def factory():
        calls.append(1)
        return {"answer": 42}

    first = context.cached("probe", factory)
    assert context.cached("probe", factory) is first
    assert len(calls) == 1


def test_cached_keys_are_independent():
    context = CheckContext(http=FakeHttp(), site_url=SITE)
    assert context.cached("a", lambda: 1) == 1
    assert context.cached("b", lambda: 2) == 2
