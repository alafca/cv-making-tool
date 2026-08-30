"""End-to-end tests that drive the real editor in a real browser.

Opt-in and slow:

    uv run pytest -m browser
    CV_MAKER_HEADED=1 uv run pytest -m browser    # watch it happen

Every test runs against a temp copy of tests/fixtures/testing_cv.yaml, so the
project's own data/ directory is never read or written.

These exist because the unit and endpoint tests cannot see the parts that break
most often in practice: whether a control is actually wired up, whether the
fields view round-trips without dropping a section, and whether the toolbar
holds still while things happen.
"""
from pathlib import Path

import pytest
import yaml

from tests.conftest import preview_text, toolbar_geometry, wait_for_preview

pytestmark = pytest.mark.browser


# --- loading -----------------------------------------------------------------


def test_editor_loads_the_fixture_cv(page):
    assert page.input_value("#f-name") == "Robin Testerson"
    assert page.locator("#cv option").count() == 2  # testing_cv + flawed
    assert wait_for_preview(page, "Robin Testerson")
    assert not page.console_errors


def test_preview_renders_every_section(page):
    assert wait_for_preview(page, "WORK EXPERIENCE")
    text = preview_text(page)
    for heading in ("PROFESSIONAL SUMMARY", "WORK EXPERIENCE", "EDUCATION",
                    "PUBLICATIONS", "SKILLS", "LANGUAGES", "CERTIFICATIONS"):
        assert heading in text.upper(), heading


def test_contact_line_shows_full_urls(page):
    assert wait_for_preview(page, "linkedin.com/in/robin-testerson")
    text = preview_text(page)
    assert "github.com/robintest" in text
    assert "+1 555 010 0134" in text


def test_photo_is_off_by_default_and_toggles_on(page):
    assert not page.is_checked("#photo")
    assert page.frame_locator("#preview").locator("img.photo").count() == 0

    page.check("#photo")
    page.wait_for_timeout(3000)
    assert page.frame_locator("#preview").locator("img.photo").count() == 1

    page.uncheck("#photo")
    page.wait_for_timeout(3000)
    assert page.frame_locator("#preview").locator("img.photo").count() == 0


# --- editing round-trip ------------------------------------------------------


def test_editing_a_field_updates_the_preview(page):
    page.fill("#f-headline", "Staff AI Engineer")
    assert wait_for_preview(page, "Staff AI Engineer")


def test_saving_writes_only_the_current_cv(page, testing_cv_dir: Path):
    before = (testing_cv_dir / "testing_cv_flawed.yaml").read_text(encoding="utf-8")
    page.fill("#f-headline", "Edited Headline")
    page.wait_for_timeout(1500)
    page.click("#saveBtn")
    page.wait_for_timeout(2000)

    saved = yaml.safe_load((testing_cv_dir / "testing_cv.yaml").read_text(encoding="utf-8"))
    assert saved["headline"] == "Edited Headline"
    assert (testing_cv_dir / "testing_cv_flawed.yaml").read_text(encoding="utf-8") == before


def test_a_fields_edit_never_drops_a_section(page, testing_cv_dir: Path):
    """The fields view rebuilds the YAML, so a section it does not model can be
    silently deleted on save. This is the regression that lost publications."""
    page.fill("#f-headline", "Still Here")
    page.wait_for_timeout(1500)
    page.click("#saveBtn")
    page.wait_for_timeout(2000)

    saved = yaml.safe_load((testing_cv_dir / "testing_cv.yaml").read_text(encoding="utf-8"))
    assert len(saved["publications"]) == 1
    assert saved["publications"][0]["url"] == "https://example.com/paper"
    assert saved["certifications"] == ["Example Cloud Practitioner"]
    assert len(saved["experience"]) == 2


def test_unknown_sections_survive_a_save(page, testing_cv_dir: Path):
    """A section added by hand, that the fields view knows nothing about."""
    path = testing_cv_dir / "testing_cv.yaml"
    path.write_text(
        path.read_text(encoding="utf-8") + "\nawards:\n  - Test Award 2024\n",
        encoding="utf-8",
    )
    page.reload(wait_until="networkidle")
    page.wait_for_selector("#fieldsView .section", timeout=30_000)

    page.fill("#f-headline", "Touched")
    page.wait_for_timeout(1500)
    page.click("#saveBtn")
    page.wait_for_timeout(2000)

    saved = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert saved["awards"] == ["Test Award 2024"]


def test_yaml_and_fields_views_agree(page):
    page.click("#btnYaml")
    page.wait_for_timeout(800)
    assert "Robin Testerson" in page.input_value("#yaml")

    page.click("#btnFields")
    page.wait_for_timeout(1200)
    assert page.input_value("#f-name") == "Robin Testerson"
    assert page.locator(".pub-item").count() == 1


# --- multiple CVs ------------------------------------------------------------


def test_duplicate_and_delete_a_cv(page, testing_cv_dir: Path):
    page.on("dialog", lambda d: d.accept("copy_cv") if d.type == "prompt" else d.accept())

    page.click("#dupCvBtn")
    page.wait_for_timeout(3000)
    assert (testing_cv_dir / "copy_cv.yaml").exists()
    assert page.input_value("#cv") == "copy_cv"
    copied = yaml.safe_load((testing_cv_dir / "copy_cv.yaml").read_text(encoding="utf-8"))
    assert copied["name"] == "Robin Testerson"

    page.click("#delCvBtn")
    page.wait_for_timeout(3000)
    assert not (testing_cv_dir / "copy_cv.yaml").exists()
    assert (testing_cv_dir / "copy_cv.yaml.bak").exists()  # recoverable
    assert (testing_cv_dir / "testing_cv.yaml").exists()


def test_switching_cv_loads_the_other_file(page):
    page.on("dialog", lambda d: d.accept())
    page.select_option("#cv", "testing_cv_flawed")
    page.wait_for_timeout(3000)
    assert page.input_value("#f-name") == "Dana Flawed"


def test_the_startup_cv_cannot_be_deleted(page):
    assert page.is_disabled("#delCvBtn")


# --- exports -----------------------------------------------------------------


def test_pdf_download_is_named_after_the_cv(page):
    with page.expect_download(timeout=60_000) as download:
        page.click("#pdfBtn")
    assert download.value.suggested_filename == "testing_cv.pdf"


def test_html_export_downloads(page):
    with page.expect_download(timeout=60_000) as download:
        page.click("#htmlBtn")
    assert download.value.suggested_filename == "testing_cv.html"


def test_copy_markdown_puts_the_cv_on_the_clipboard(page):
    page.click("#mdBtn")
    page.wait_for_timeout(2000)
    markdown = page.evaluate("navigator.clipboard.readText()")
    assert markdown.startswith("# Robin Testerson")
    assert "## Work Experience" in markdown
    assert "[linkedin.com/in/robin-testerson]" in markdown
    assert "<a href" not in markdown  # markdown, not HTML


# --- ATS panel ---------------------------------------------------------------


def test_ats_panel_reports_a_clean_fixture(page):
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)

    assert page.locator(".sev-critical").count() == 0
    assert page.locator(".sev-good").count() > 0
    score = int(page.inner_text("#atsScore").split("/")[0])
    assert score >= 80, f"clean fixture scored {score}"


def test_ats_panel_reports_the_flawed_fixture(page):
    page.on("dialog", lambda d: d.accept())
    page.select_option("#cv", "testing_cv_flawed")
    page.wait_for_timeout(3000)

    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)
    body = page.inner_text("#atsBody").lower()

    assert "phone" in body
    assert "acronym" in body
    assert "availability" in body
    score = int(page.inner_text("#atsScore").split("/")[0])
    assert score < 70, f"flawed fixture scored {score}"


def test_ats_panel_matches_a_job_description(page):
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)

    page.fill("#atsJd", "We need Kubernetes, Terraform and vector database work.")
    page.dispatch_event("#atsJd", "change")
    page.wait_for_timeout(8000)

    body = page.inner_text("#atsBody").lower()
    assert "kubernetes" in body
    assert "terraform" in body
    assert "vector database" in body  # reported as one term, not two


def test_ats_panel_does_not_disturb_the_toolbar(page):
    before = toolbar_geometry(page)
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)
    assert toolbar_geometry(page) == before

    page.click("#atsClose")
    page.wait_for_timeout(500)
    assert not page.locator("#atsPanel").is_visible()


# --- toolbar stability -------------------------------------------------------


@pytest.mark.parametrize("button", ["#saveBtn", "#mdBtn"])
def test_toast_never_moves_the_buttons(page, button):
    """The status message used to live in the toolbar's flex row, so every
    click shifted the buttons or wrapped the bar onto a second line."""
    before = toolbar_geometry(page)
    page.click(button)
    page.wait_for_timeout(1200)

    assert float(page.evaluate(
        "getComputedStyle(document.getElementById('status')).opacity"
    )) > 0.5
    assert toolbar_geometry(page) == before


def test_no_console_errors_across_a_session(page):
    """One pass over the main controls, asserting the page stays clean."""
    page.fill("#f-headline", "Session Test")
    page.wait_for_timeout(1200)
    page.click("#btnYaml")
    page.wait_for_timeout(600)
    page.click("#btnFields")
    page.wait_for_timeout(1200)
    page.click("#saveBtn")
    page.wait_for_timeout(1500)
    page.click("#mdBtn")
    page.wait_for_timeout(1500)
    assert not page.console_errors, page.console_errors
