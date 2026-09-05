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


def test_toolbar_stays_usable_while_the_ats_panel_is_open(page):
    """The panel is a right-hand overlay and the buttons live on the right, so
    an untimed z-index makes Save unreachable while the report is up."""
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)
    assert page.locator("#atsPanel").is_visible()

    for selector in ("#saveBtn", "#pdfBtn", "#htmlBtn", "#mdBtn", "#atsBtn"):
        # A trial click raises if anything is intercepting the pointer.
        page.click(selector, trial=True, timeout=5_000)

    # And a real click still works.
    page.click("#saveBtn")
    page.wait_for_timeout(1500)
    assert float(page.evaluate(
        "getComputedStyle(document.getElementById('status')).opacity"
    )) > 0.5


def test_phone_is_editable_and_survives_a_save(page, testing_cv_dir: Path):
    """The fields view rebuilds contact wholesale, so a contact key with no
    input is deleted on save. phone was rendered and checked, but uneditable."""
    assert page.input_value("#f-phone") == "+1 555 010 0134"

    page.fill("#f-phone", "+1 555 010 0199")
    page.wait_for_timeout(1500)
    page.click("#saveBtn")
    page.wait_for_timeout(2000)

    saved = yaml.safe_load((testing_cv_dir / "testing_cv.yaml").read_text(encoding="utf-8"))
    assert saved["contact"]["phone"] == "+1 555 010 0199"
    assert wait_for_preview(page, "+1 555 010 0199")


def test_ats_check_sees_the_phone_from_the_editor(page):
    """End to end: what the browser sends must still carry contact.phone."""
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)
    assert "no phone number" not in page.inner_text("#atsBody").lower()


# --- styles ------------------------------------------------------------------


def _preview_font(page) -> str:
    return page.frame_locator("#preview").locator("body").evaluate(
        "el => getComputedStyle(el).fontFamily"
    )


def test_style_dropdown_offers_every_style(page):
    from cv_maker.generator import STYLES

    options = page.locator("#style option")
    assert options.count() == len(STYLES)
    assert options.evaluate_all("els => els.map(e => e.value)") == list(STYLES)
    assert page.input_value("#style") == "classic"
    assert page.input_value("#f-style") == "classic"


def test_choosing_a_style_restyles_the_preview_and_lands_in_the_yaml(page, testing_cv_dir: Path):
    assert wait_for_preview(page, "Robin Testerson")
    assert "Cambria" not in _preview_font(page)

    page.select_option("#style", "cambria")
    page.wait_for_timeout(2500)
    assert "Cambria" in _preview_font(page)
    assert page.input_value("#f-style") == "cambria"

    page.click("#btnYaml")
    page.wait_for_timeout(600)
    assert "style: cambria" in page.input_value("#yaml")

    page.click("#saveBtn")
    page.wait_for_timeout(2000)
    saved = yaml.safe_load((testing_cv_dir / "testing_cv.yaml").read_text(encoding="utf-8"))
    assert saved["meta"]["style"] == "cambria"
    assert saved["meta"]["lang"] == "en"  # the rest of meta survives
    assert not page.console_errors


def test_style_can_be_chosen_from_the_yaml_view(page):
    page.click("#btnYaml")
    page.wait_for_timeout(600)
    page.select_option("#style", "segoe")
    page.wait_for_timeout(2500)
    assert "style: segoe" in page.input_value("#yaml")
    assert "Segoe UI" in _preview_font(page)

    page.select_option("#style", "classic")
    page.wait_for_timeout(2500)
    assert "style:" not in page.input_value("#yaml")  # classic is the default
    assert "Segoe UI" not in _preview_font(page)


def test_style_select_follows_the_loaded_cv(page, testing_cv_dir: Path):
    path = testing_cv_dir / "testing_cv_flawed.yaml"
    path.write_text(
        path.read_text(encoding="utf-8").replace("meta:\n", "meta:\n  style: segoe\n", 1),
        encoding="utf-8",
    )
    page.on("dialog", lambda d: d.accept())
    page.select_option("#cv", "testing_cv_flawed")
    page.wait_for_timeout(3000)
    assert page.input_value("#style") == "segoe"
    assert "Segoe UI" in _preview_font(page)

    page.select_option("#cv", "testing_cv")
    page.wait_for_timeout(3000)
    assert page.input_value("#style") == "classic"


def test_languages_render_on_one_line(page):
    assert wait_for_preview(page, "LANGUAGES")
    items = page.frame_locator("#preview").locator(".languages-line li")
    assert items.count() >= 2
    tops = items.evaluate_all("els => els.map(e => Math.round(e.getBoundingClientRect().top))")
    assert len(set(tops)) == 1, tops


def test_style_dropdown_does_not_move_the_buttons(page):
    before = toolbar_geometry(page)
    page.select_option("#style", "cambria")
    page.wait_for_timeout(2500)
    assert toolbar_geometry(page) == before


def test_bold_markup_in_a_bullet_renders_bold(page):
    bullets = page.locator(".exp-item .exp-bullets").first
    bullets.fill("Built a **document retrieval service** over 40,000 records.")
    page.wait_for_timeout(2500)
    strong = page.frame_locator("#preview").locator("li strong")
    assert strong.count() == 1
    assert strong.first.inner_text() == "document retrieval service"
    assert "**" not in preview_text(page)


# --- second opinions ----------------------------------------------------------


def test_ats_panel_shows_one_chip_per_checker(page):
    """Three scores side by side: ours, the Lever-style parse, and
    ats-resume-scorer. The suite runs with the external checkers off, so the
    two outside chips say why they have no score rather than vanish."""
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)

    chips = page.locator(".ats-chip")
    assert chips.count() == 3
    labels = chips.locator(".chip-label").all_inner_texts()
    assert labels == ["Built-in", "Lever-style parse", "ats-resume-scorer"]
    assert "/100" in chips.nth(0).inner_text()
    assert "no score" in chips.nth(1).inner_text()
    assert "no score" in chips.nth(2).inner_text()

    blocks = page.locator(".ats-external")
    assert blocks.count() == 2
    assert "CV_MAKER_EXTERNAL_ATS" in blocks.nth(0).inner_text()
    assert not page.console_errors


def test_experience_dates_sit_below_the_employer_and_stay_editable(page):
    assert wait_for_preview(page, "Testworks")
    frame = page.frame_locator("#preview")
    title = frame.locator('[data-edit="experience.0.title"]').bounding_box()
    company = frame.locator('[data-edit="experience.0.company"]').bounding_box()
    dates = frame.locator('[data-edit="experience.0.dates"]').bounding_box()
    assert title["y"] < company["y"] < dates["y"]
    assert abs(dates["x"] - title["x"]) < 2  # flush left, not at the right margin

    page.fill(".exp-item .exp-dates >> nth=0", "Jan 2021 - Present")
    assert wait_for_preview(page, "Jan 2021 - Present")


# --- editor accessibility and polish -----------------------------------------


def _contrast(page, selector: str) -> float:
    """WCAG contrast of an element's text against its own background."""
    return page.evaluate("""(sel) => {
        const el = document.querySelector(sel);
        const cs = getComputedStyle(el);
        const parse = c => c.match(/[\d.]+/g).slice(0, 3).map(Number);
        const lum = ([r, g, b]) => {
            const f = v => { v /= 255; return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4; };
            return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
        };
        // Walk up until a painted background is found.
        let bg = el, bgc = cs.backgroundColor;
        while (bg && /rgba\(0, 0, 0, 0\)|transparent/.test(bgc)) { bg = bg.parentElement; bgc = bg ? getComputedStyle(bg).backgroundColor : 'rgb(255,255,255)'; }
        const a = lum(parse(cs.color)), b = lum(parse(bgc));
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    }""", selector)


def test_every_field_has_a_bound_label(page):
    """A screen reader names a field by its label; a placeholder or a nearby
    span is not a name. Every visible control in the fields view must have one."""
    unnamed = page.evaluate("""() => [...document.querySelectorAll('#fieldsView input, #fieldsView textarea, #fieldsView select')]
        .filter(el => el.getBoundingClientRect().width > 0)
        .filter(el => !(el.labels && el.labels.length) && !el.getAttribute('aria-label'))
        .map(el => el.id || el.className)""")
    assert unnamed == []
    assert page.evaluate("document.querySelector('label[for=\"f-name\"]').innerText") == "Name"
    assert page.get_attribute("#yaml", "aria-label")
    assert page.get_attribute("#preview", "title")


def test_keyboard_focus_is_visible_on_fields_and_yaml(page):
    page.click("#f-name")
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "f-headline"
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"

    page.click("#btnYaml")
    page.wait_for_timeout(500)
    page.focus("#atsBtn")  # last toolbar control; the next Tab stop is the YAML pane
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement.id") == "yaml"
    assert page.evaluate("getComputedStyle(document.activeElement).outlineStyle") != "none"


def test_toolbar_text_contrast_meets_aa(page):
    for sel in ("#saveBtn", "#pdfBtn", "#htmlBtn", "#mdBtn", "#atsBtn", "#delCvBtn",
                "#btnFields", "#btnYaml", ".link-hint", ".add-btn", ".remove-btn"):
        ratio = _contrast(page, sel)
        assert ratio >= 4.5, f"{sel}: {ratio:.2f}"


def test_only_save_is_a_filled_action(page):
    fills = page.evaluate("""() => [...document.querySelectorAll('.toolbar .group[aria-label="Actions"] button')]
        .map(b => [b.id, getComputedStyle(b).backgroundColor])""")
    filled = [i for i, c in fills if c != fills[1][1]]
    assert filled == ["saveBtn"], fills


def test_ats_panel_is_a_dialog_that_closes_on_escape(page):
    page.click("#atsBtn")
    page.wait_for_selector(".ats-finding", timeout=90_000)
    assert page.get_attribute("#atsPanel", "role") == "dialog"
    assert page.evaluate("document.activeElement.id") == "atsTitle"

    page.keyboard.press("Escape")
    page.wait_for_timeout(300)
    assert not page.locator("#atsPanel").is_visible()
    assert page.evaluate("document.activeElement.id") == "atsBtn"


def test_splitter_resizes_with_the_keyboard(page):
    before = page.locator(".editor-pane").bounding_box()["width"]
    page.focus("#splitter")
    page.keyboard.press("ArrowLeft")
    page.wait_for_timeout(200)
    after = page.locator(".editor-pane").bounding_box()["width"]
    assert after < before - 20
    assert page.get_attribute("#splitter", "role") == "separator"


def test_row_buttons_are_named_and_large_enough(page):
    buttons = page.locator(".exp-item .item-actions button")
    assert buttons.count() == 6
    names = buttons.evaluate_all("els => els.map(e => e.getAttribute('aria-label'))")
    assert names[:3] == ["Move experience entry up", "Move experience entry down", "Remove experience entry"]
    for box in buttons.evaluate_all("els => els.map(e => e.getBoundingClientRect())"):
        assert box["width"] >= 24 and box["height"] >= 24


def test_error_toast_stays_until_dismissed(page):
    page.click("#btnYaml")
    page.wait_for_timeout(500)
    page.fill("#yaml", "name: [broken")
    page.click("#btnFields")
    page.wait_for_timeout(3500)  # a success toast would have faded by now
    assert page.locator("#status").evaluate("el => getComputedStyle(el).opacity") == "1"
    assert "Invalid YAML" in page.inner_text("#status")

    page.click("#status .toast-close")
    page.wait_for_timeout(400)
    assert page.locator("#status").evaluate("el => getComputedStyle(el).opacity") == "0"


def test_reduced_motion_removes_the_toast_slide(page):
    page.emulate_media(reduced_motion="reduce")
    props = page.evaluate("getComputedStyle(document.getElementById('status')).transitionProperty")
    assert "transform" not in props
    page.emulate_media(reduced_motion="no-preference")
    props = page.evaluate("getComputedStyle(document.getElementById('status')).transitionProperty")
    assert "transform" in props


def test_page_has_landmarks_and_a_heading_outline(page):
    assert page.locator("main").count() == 1
    assert page.locator("h1").count() == 1
    assert page.inner_text("h1") == "CV Maker"
    assert page.locator("#fieldsView h2.section-title").count() >= 6
    assert page.get_attribute("#btnFields", "aria-pressed") == "true"
    page.click("#btnYaml")
    assert page.get_attribute("#btnYaml", "aria-pressed") == "true"
    assert page.get_attribute("#btnFields", "aria-pressed") == "false"
