from app.adapters.browser.chrome_locator import chrome_installed

MAC = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"


def locate(platform="darwin", environ=None, existing=(), on_path=()):
    return chrome_installed(
        environ=environ or {},
        exists=lambda path: path in existing,
        which=lambda name: f"/usr/bin/{name}" if name in on_path else None,
        platform=platform,
    )


def test_chrome_in_the_standard_macos_location_is_found():
    assert locate("darwin", existing={MAC}) is True


def test_chrome_in_the_user_applications_folder_on_macos_is_found():
    home_path = "/Users/me/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    assert locate("darwin", environ={"HOME": "/Users/me"}, existing={home_path}) is True


def test_linux_finds_known_binary_names_on_the_path():
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        assert locate("linux", on_path={name}) is True


def test_windows_standard_locations_are_checked():
    path = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    assert locate("win32", environ={"PROGRAMFILES": r"C:\Program Files"}, existing={path}) is True


def test_nothing_installed_means_not_found_on_every_platform():
    for platform in ("darwin", "linux", "win32"):
        assert locate(platform) is False


def test_environment_variables_with_an_existing_file_count_as_found():
    for variable in ("CHROME_BIN", "SE_BROWSER_PATH", "SE_CHROME_PATH"):
        assert locate(
            "linux", environ={variable: "/opt/chrome/chrome"}, existing={"/opt/chrome/chrome"}
        )


def test_environment_variable_pointing_nowhere_does_not_count():
    assert locate("linux", environ={"CHROME_BIN": "/nonexistent/chrome"}) is False
