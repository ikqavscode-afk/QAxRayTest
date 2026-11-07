import pytest

from app.core.browser import DESKTOP, BrowserUnavailableError, ImageInfo, ImagesSnapshot
from app.core.browser_session import BrowserSession
from app.core.checks.context import CheckContext
from app.core.checks.images import MAX_IMAGES, ImagesLoad
from app.schemas.result import Severity
from app.schemas.status import CheckStatus
from tests.unit.support import SITE, FakeBrowser, FakeFactory, FakeHttp

CDN = "https://cdn.example.com/img"


def image(name="a.png", complete=True, width=100, lazy=False):
    return ImageInfo(src=f"{CDN}/{name}", complete=complete, natural_width=width, lazy=lazy)


def run(*items, total=None):
    browser = FakeBrowser()
    browser.images_snapshot = ImagesSnapshot(
        total=len(items) if total is None else total, items=items
    )
    session = BrowserSession(FakeFactory(browser), SITE)
    context = CheckContext(http=FakeHttp(), site_url=SITE, browser=session)
    return ImagesLoad().run(context), browser


def test_passes_when_every_visible_image_loaded():
    result, browser = run(image("a.png"), image("b.jpg"))
    assert result.check_id == "QC-009"
    assert result.status is CheckStatus.PASSED
    assert result.severity is Severity.MEDIUM
    assert "2" in result.actual
    assert browser.opens[0][1] == DESKTOP
    assert browser.images_limits == [MAX_IMAGES] == [30]


def test_broken_image_is_a_warning_naming_the_file_and_the_count():
    result, _ = run(image("ok.png"), image("broken.png", width=0), image("fine.png"))
    assert result.status is CheckStatus.WARNING
    assert "broken.png" in result.actual and "1 из 3" in result.actual
    assert "ok.png" not in result.actual
    assert result.reason


def test_image_that_never_finished_loading_is_counted_as_broken():
    result, _ = run(image("slow.png", complete=False, width=0))
    assert result.status is CheckStatus.WARNING


def test_the_check_never_fails_it_is_a_heuristic():
    result, _ = run(*[image(f"{n}.png", width=0) for n in range(10)])
    assert result.status is not CheckStatus.FAILED


@pytest.mark.parametrize(
    "src",
    [f"{CDN}/logo.svg", "data:image/svg+xml", f"{CDN}/Icon.SVG"],
)
def test_svg_without_intrinsic_size_is_not_considered_broken(src):
    svg = ImageInfo(src=src, complete=True, natural_width=0, lazy=False)
    result, _ = run(svg)
    assert result.status is CheckStatus.PASSED


def test_lazy_images_that_have_not_loaded_yet_are_not_judged():
    result, _ = run(image("a.png"), image("far.png", complete=False, width=0, lazy=True))
    assert result.status is CheckStatus.PASSED
    assert "лен" in result.actual  # сообщает, что часть не оценивалась


def test_only_lazy_unloaded_images_means_nothing_to_judge():
    result, _ = run(image("far.png", complete=False, width=0, lazy=True))
    assert result.status is CheckStatus.SKIPPED
    assert result.reason


def test_no_visible_images_is_skipped_with_a_reason():
    result, _ = run()
    assert result.status is CheckStatus.SKIPPED
    assert "изображен" in result.reason


def test_report_says_when_only_the_first_images_were_checked():
    result, _ = run(*[image(f"{n}.png") for n in range(30)], total=45)
    assert result.status is CheckStatus.PASSED
    assert "30 из 45" in result.actual


def test_long_addresses_are_shortened_in_the_report():
    long_name = "x" * 200 + ".png"
    result, _ = run(image(long_name, width=0))
    assert len(result.actual) < 250


def test_without_a_connected_browser_the_error_reaches_the_runner():
    context = CheckContext(http=FakeHttp(), site_url=SITE)
    with pytest.raises(BrowserUnavailableError):
        ImagesLoad().run(context)
