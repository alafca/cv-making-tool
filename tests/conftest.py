"""Shared fixtures, including the browser harness for the UI tests.

The browser tests are marked `browser` and deselected by default, so the fast
suite stays fast:

    uv run pytest                 # unit + endpoint tests
    uv run pytest -m browser      # drive the real editor in a real browser
    uv run pytest -m ""           # everything

They run headless unless CV_MAKER_HEADED=1 is set, and they never read or write
the project's data/ directory: each test gets a temp copy of
tests/fixtures/testing_cv.yaml.
"""
from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest
import yaml
from PIL import Image

FIXTURES = Path(__file__).parent / "fixtures"
TESTING_CV = FIXTURES / "testing_cv.yaml"
FLAWED_CV = FIXTURES / "testing_cv_flawed.yaml"

# Browsers Playwright can drive without downloading one. Brave first: it is
# what this machine has installed.
_BROWSER_CANDIDATES = [
    r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files (x86)\BraveSoftware\Brave-Browser\Application\brave.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
]


def pytest_configure(config):
    config.addinivalue_line(
        "markers", "browser: drives the editor in a real browser (opt-in, slow)"
    )


def pytest_collection_modifyitems(config, items):
    """Skip browser tests unless they were asked for by name or marker."""
    if config.option.markexpr and "browser" in config.option.markexpr:
        return
    skip = pytest.mark.skip(reason="browser test; run with -m browser")
    for item in items:
        if "browser" in item.keywords:
            item.add_marker(skip)


def testing_cv_data() -> dict:
    """The fixture CV as parsed data, for tests that do not need a file."""
    return yaml.safe_load(TESTING_CV.read_text(encoding="utf-8"))


def flawed_cv_data() -> dict:
    return yaml.safe_load(FLAWED_CV.read_text(encoding="utf-8"))


@pytest.fixture()
def testing_cv_dir(tmp_path: Path) -> Path:
    """A throwaway data directory holding the fixture CVs.

    Never the project's own data/: a test may create, edit and delete CVs here
    without any chance of touching real content.
    """
    (tmp_path / "testing_cv.yaml").write_text(
        TESTING_CV.read_text(encoding="utf-8"), encoding="utf-8"
    )
    (tmp_path / "testing_cv_flawed.yaml").write_text(
        FLAWED_CV.read_text(encoding="utf-8"), encoding="utf-8"
    )
    Image.new("RGB", (40, 40), (90, 110, 140)).save(tmp_path / "testing_photo.png")
    return tmp_path


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture()
def live_server(testing_cv_dir: Path):
    """The editor running against the fixture data, on its own port."""
    import uvicorn

    from cv_maker.server import create_app

    port = _free_port()
    config = uvicorn.Config(
        create_app(testing_cv_dir / "testing_cv.yaml"),
        host="127.0.0.1",
        port=port,
        log_level="error",
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 30
    while time.time() < deadline:
        if server.started:
            break
        time.sleep(0.1)
    else:  # pragma: no cover - only on a very slow machine
        pytest.fail("the editor server did not start")

    yield url

    server.should_exit = True
    thread.join(timeout=10)


def _browser_executable() -> str:
    for candidate in _BROWSER_CANDIDATES:
        if Path(candidate).exists():
            return candidate
    pytest.skip("no Chromium-based browser found to drive")


@pytest.fixture()
def page(live_server: str):
    """A browser page on the editor, with console errors collected.

    Yields the Playwright page with two extras attached:
      page.console_errors - list of real errors (the favicon 404 is filtered,
                            since the editor ships no favicon on purpose)
      page.base_url       - the server's URL
    """
    playwright = pytest.importorskip("playwright.sync_api")

    with playwright.sync_playwright() as pw:
        browser = pw.chromium.launch(
            executable_path=_browser_executable(),
            headless=os.environ.get("CV_MAKER_HEADED") != "1",
        )
        context = browser.new_context(
            viewport={"width": 1500, "height": 950},
            permissions=["clipboard-read", "clipboard-write"],
            accept_downloads=True,
        )
        page = context.new_page()

        errors: list[str] = []
        page.on(
            "console",
            lambda m: errors.append(f"{m.text} @ {m.location}")
            if m.type == "error" and "favicon" not in str(m.location)
            else None,
        )
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.console_errors = errors
        page.base_url = live_server

        page.goto(live_server, wait_until="networkidle")
        # The editor loads its config, CV list and YAML before switching to the
        # fields view; wait for that rather than a fixed sleep.
        page.wait_for_selector("#fieldsView .section", timeout=30_000)

        yield page

        browser.close()


def preview_text(page) -> str:
    """The rendered CV's text, from inside the preview iframe."""
    return page.frame_locator("#preview").locator("body").inner_text()


def wait_for_preview(page, needle: str, timeout: float = 30.0) -> bool:
    """Wait for the server-rendered preview to contain `needle`.

    Case-insensitive: CSS uppercases the section headings, so a title-case
    search would miss them.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if needle.lower() in preview_text(page).lower():
                return True
        except Exception:
            pass
        page.wait_for_timeout(250)
    return False


def toolbar_geometry(page) -> dict:
    """Toolbar height and button positions, for asserting nothing reflows."""
    buttons = ["#saveBtn", "#pdfBtn", "#htmlBtn", "#mdBtn", "#atsBtn"]
    bar = page.locator(".toolbar").bounding_box()
    out = {"height": round(bar["height"], 1)}
    for selector in buttons:
        box = page.locator(selector).bounding_box()
        out[selector] = (round(box["x"], 1), round(box["y"], 1))
    return out
