"""Endpoint tests for the CV Maker editor server.

Uses synthetic CV data (not the real data/ files) so tests are independent
of personal CV content.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cv_maker.generator import (
    cv_file_path,
    cv_lang,
    linkify,
    list_cvs,
    render_markdown,
    validate_cv_name,
)
from cv_maker.server import create_app

SAMPLE_YAML = """\
name: Jane Doe
headline: Test Engineer
contact:
  email: jane@example.com
  location: Testville
  linkedin:
    text: jane-doe
    url: https://linkedin.com/in/jane-doe
  github: janedoe
meta:
  photo: photo.png
  lang: es
summary: A summary with a [link](https://example.com) in it.
experience:
  - title: Engineer
    company: ACME
    location: Remote
    dates: 2020 - Present
    description: Doing things.
    bullets:
      - Did a thing; [see here](https://example.com/thing).
education:
  - institution: Test University
    degree: BSc Testing
    dates: 2016 - 2020
skills:
  - category: Tools
    entries: pytest, coverage
publications:
  - title: A Paper About Testing
    venue: Journal of Tests
    year: 2024
    url: https://example.com/paper
    note: co-author
languages:
  - name: English
    level: native
certifications:
  - Certified Tester
"""


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    (tmp_path / "cv_data.yaml").write_text(SAMPLE_YAML, encoding="utf-8")
    # RGBA on purpose: exercises the RGB conversion before JPEG encoding.
    Image.new("RGBA", (20, 20), (200, 30, 30, 255)).save(tmp_path / "photo.png")
    return tmp_path


@pytest.fixture()
def client(data_dir: Path) -> TestClient:
    return TestClient(create_app(data_dir / "cv_data.yaml"))


# --- Language comes from the CV, not from the server -------------------------


def test_cv_lang_defaults_to_english():
    assert cv_lang({}) == "en"
    assert cv_lang({"meta": None}) == "en"
    assert cv_lang({"meta": {}}) == "en"
    assert cv_lang({"meta": {"lang": "es"}}) == "es"


def test_headers_follow_the_cv_meta_lang(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML})
    assert "Experiencia Laboral" in r.text  # meta.lang: es

    english = SAMPLE_YAML.replace("  lang: es", "  lang: en")
    r = client.post("/api/preview", json={"yaml": english})
    assert "Work Experience" in r.text


def test_missing_or_unknown_meta_lang_renders_english(client):
    r = client.post("/api/preview", json={"yaml": "name: X\nsummary: hi\n"})
    assert "Professional Summary" in r.text

    weird = SAMPLE_YAML.replace("  lang: es", "  lang: fr")
    r = client.post("/api/preview", json={"yaml": weird})
    assert "Work Experience" in r.text


def test_config_reports_cv_defaults(client):
    d = client.get("/api/config").json()
    assert d["default_cv"] == "cv_data"
    assert set(d["langs"]) == {"es", "en"}
    assert "default_lang" not in d


# --- Reading, previewing, exporting ------------------------------------------


def test_read_yaml(client):
    r = client.get("/api/yaml")
    assert r.status_code == 200
    assert "Jane Doe" in r.json()["yaml"]
    assert r.json()["cv"] == "cv_data"


def test_read_unknown_cv_returns_404(client):
    assert client.get("/api/yaml", params={"cv": "ghost"}).status_code == 404


def test_read_rejects_path_traversal(client):
    r = client.get("/api/yaml", params={"cv": "../secret"})
    assert r.status_code == 400
    assert "Invalid CV name" in r.json()["detail"]


def test_preview_renders_html_with_photo_and_links(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML, "show_photo": True})
    assert r.status_code == 200
    assert "<h1" in r.text
    assert "data:image/jpeg;base64" in r.text  # RGBA png converted fine
    assert '<a href="https://example.com/thing">see here</a>' in r.text


def test_photo_is_omitted_by_default(client):
    """US-style resumes carry no photo, so it must be opt-in."""
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML})
    assert r.status_code == 200
    assert "data:image/jpeg;base64" not in r.text
    assert "Jane Doe" in r.text  # everything else still renders


def test_pdf_omits_the_photo_by_default(client):
    with_photo = client.post(
        "/api/pdf", json={"yaml": SAMPLE_YAML, "show_photo": True}
    ).content
    without = client.post("/api/pdf", json={"yaml": SAMPLE_YAML}).content
    assert len(without) < len(with_photo)


def test_preview_escapes_contact_fields(client):
    evil = SAMPLE_YAML.replace("jane@example.com", '"<b>x</b>&y"')
    r = client.post("/api/preview", json={"yaml": evil})
    assert "&lt;b&gt;x&lt;/b&gt;&amp;y" in r.text
    assert "<b>x</b>" not in r.text


def test_preview_invalid_yaml_shows_error_page(client):
    r = client.post("/api/preview", json={"yaml": "a: [unclosed"})
    assert r.status_code == 200
    assert "Invalid YAML" in r.text
    assert "x-page-count" not in r.headers


def test_preview_non_dict_yaml_shows_error_page(client):
    r = client.post("/api/preview", json={"yaml": "just a string"})
    assert r.status_code == 200
    assert "error" in r.text.lower()


def test_preview_reports_page_count(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML})
    assert int(r.headers["x-page-count"]) >= 1


@pytest.mark.parametrize("body", ["name: Minimal", "name: Minimal\nmeta:\n"])
def test_render_survives_missing_or_empty_meta(client, body):
    """A hand-written or freshly created CV need not carry a meta: block."""
    r = client.post("/api/preview", json={"yaml": body})
    assert r.status_code == 200
    assert "Minimal" in r.text
    assert "error" not in r.text.lower()
    assert client.post("/api/pdf", json={"yaml": body}).status_code == 200


def test_pdf_returns_pdf_bytes(client):
    r = client.post("/api/pdf", json={"yaml": SAMPLE_YAML})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_pdf_invalid_yaml_returns_400(client):
    assert client.post("/api/pdf", json={"yaml": "a: [unclosed"}).status_code == 400


def test_export_html(client):
    r = client.post("/api/export-html", json={"yaml": SAMPLE_YAML})
    assert r.status_code == 200
    assert "<title>Jane Doe - CV</title>" in r.text


def test_downloads_are_named_after_the_cv(client):
    r = client.post("/api/pdf", json={"yaml": SAMPLE_YAML, "cv": "acme"})
    assert r.headers["content-disposition"] == "attachment; filename=acme.pdf"
    r = client.post("/api/export-html", json={"yaml": SAMPLE_YAML})
    assert r.headers["content-disposition"] == "attachment; filename=cv_data.html"


def test_preview_renders_publications(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML})
    assert "A Paper About Testing" in r.text
    assert '<a href="https://example.com/paper">A Paper About Testing</a>' in r.text


def test_publications_section_absent_when_no_data(client):
    r = client.post("/api/preview", json={"yaml": "name: X\nsummary: none\n"})
    assert "Publications" not in r.text


def test_publication_fields_are_escaped(client):
    body = 'name: X\npublications:\n  - title: "<script>alert(1)</script>"\n    venue: J\n    year: 2020\n'
    r = client.post("/api/preview", json={"yaml": body})
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


# --- Saving ------------------------------------------------------------------


def test_save_invalid_yaml_rejected_and_file_untouched(client, data_dir):
    before = (data_dir / "cv_data.yaml").read_text(encoding="utf-8")
    r = client.post("/api/save", json={"yaml": "a: [unclosed"})
    assert r.status_code == 400
    assert (data_dir / "cv_data.yaml").read_text(encoding="utf-8") == before


def test_save_writes_backup_of_previous_content(client, data_dir):
    r = client.post("/api/save", json={"yaml": "name: New Version"})
    assert r.status_code == 200
    assert (data_dir / "cv_data.yaml.bak").read_text(encoding="utf-8") == SAMPLE_YAML
    assert "New Version" in (data_dir / "cv_data.yaml").read_text(encoding="utf-8")


def test_editor_page_is_not_cacheable(client):
    """A stale editor page writes YAML using its own idea of the schema."""
    assert "no-store" in client.get("/").headers.get("cache-control", "")


def test_save_keeps_a_timestamped_history_snapshot(client, data_dir):
    client.post("/api/save", json={"yaml": "name: V2"})
    snaps = list((data_dir / ".history").glob("cv_data-*.yaml"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == SAMPLE_YAML


def test_two_saves_do_not_destroy_the_original(client, data_dir):
    """The .bak alone loses the good copy after a second save; history must not."""
    client.post("/api/save", json={"yaml": "name: V2"})
    client.post("/api/save", json={"yaml": "name: V3"})

    assert "V2" in (data_dir / "cv_data.yaml.bak").read_text(encoding="utf-8")
    history = sorted((data_dir / ".history").glob("cv_data-*.yaml"))
    assert any(
        SAMPLE_YAML == s.read_text(encoding="utf-8") for s in history
    ), "original content must remain recoverable from history"


def test_history_is_capped(client, data_dir, monkeypatch):
    import cv_maker.server as srv

    monkeypatch.setattr(srv, "HISTORY_KEEP", 3)
    for i in range(6):
        # Distinct timestamps are not guaranteed within a test run, so write
        # the snapshots directly through the helper being exercised.
        (data_dir / "cv_data.yaml").write_text(f"name: V{i}", encoding="utf-8")
        srv._backup(data_dir / "cv_data.yaml")
        for n, snap in enumerate(sorted((data_dir / ".history").glob("cv_data-*.yaml"))):
            snap.rename(snap.with_name(f"cv_data-2026010{n}-000000.yaml"))
    assert len(list((data_dir / ".history").glob("cv_data-*.yaml"))) <= 3


def test_history_dir_is_not_mistaken_for_a_cv(client, data_dir):
    client.post("/api/save", json={"yaml": "name: V2"})
    assert client.get("/api/cvs").json()["cvs"] == ["cv_data"]


# --- Markdown export ---------------------------------------------------------


def test_markdown_endpoint_renders_the_whole_cv(client):
    english = SAMPLE_YAML.replace("  lang: es", "  lang: en")
    r = client.post("/api/markdown", json={"yaml": english})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/markdown")
    md = r.text
    assert md.startswith("# Jane Doe")
    for heading in ("## Professional Summary", "## Work Experience",
                    "## Education", "## Publications", "## Skills",
                    "## Languages", "## Certifications"):
        assert heading in md, heading
    assert "### Engineer — ACME" in md
    assert "- Did a thing" in md


def test_markdown_keeps_inline_links_verbatim(client):
    """Bullets already use [text](url); markdown must not HTML-escape them."""
    md = client.post("/api/markdown", json={"yaml": SAMPLE_YAML}).text
    assert "[see here](https://example.com/thing)" in md
    assert "<a href" not in md
    assert "&lt;" not in md


def test_markdown_publication_becomes_a_linked_entry(client):
    md = client.post("/api/markdown", json={"yaml": SAMPLE_YAML}).text
    assert "- **[A Paper About Testing](https://example.com/paper)**" in md
    assert "Journal of Tests, 2024" in md
    assert "*(co-author)*" in md


def test_markdown_headers_follow_meta_lang(client):
    md = client.post("/api/markdown", json={"yaml": SAMPLE_YAML}).text
    assert "## Experiencia Laboral" in md  # meta.lang: es


def test_markdown_invalid_yaml_returns_400(client):
    assert client.post("/api/markdown", json={"yaml": "a: [unclosed"}).status_code == 400


def test_markdown_contact_handles_string_and_mapping_handles(client):
    md = client.post("/api/markdown", json={"yaml": SAMPLE_YAML}).text
    # Labels are URL-shaped in markdown too, so a CV pasted into a chat carries
    # the same machine-readable contact details as the PDF.
    assert "[linkedin.com/in/jane-doe](https://linkedin.com/in/jane-doe)" in md
    assert "[github.com/janedoe](https://github.com/janedoe)" in md


def test_render_markdown_skips_absent_sections():
    assert render_markdown({"name": "Only A Name"}).strip() == "# Only A Name"


# --- Multiple CVs ------------------------------------------------------------


@pytest.mark.parametrize("name", ["acme", "acme-2", "a_b", "X9", "cv_data_en"])
def test_validate_cv_name_accepts_safe_names(name):
    """A trailing _en is just a name now that language variants are gone."""
    assert validate_cv_name(name) == name


@pytest.mark.parametrize(
    "name",
    ["../escape", "sub/dir", "sub\\dir", "with space", "dot.name", "", "-leading"],
)
def test_validate_cv_name_rejects_unsafe(name):
    with pytest.raises(ValueError):
        validate_cv_name(name)


def test_cv_file_path_is_one_file_per_cv(tmp_path):
    assert cv_file_path(tmp_path, "acme") == tmp_path / "acme.yaml"
    assert cv_file_path(tmp_path, "acme_en") == tmp_path / "acme_en.yaml"


def test_list_cvs_returns_every_yaml_file_as_its_own_cv(tmp_path):
    for fname in ["cv_data.yaml", "cv_data_en.yaml", "acme.yaml"]:
        (tmp_path / fname).write_text("name: x", encoding="utf-8")
    (tmp_path / "not a cv.yaml").write_text("name: x", encoding="utf-8")
    (tmp_path / "cv_data.yaml.bak").write_text("name: old", encoding="utf-8")

    assert list_cvs(tmp_path) == ["acme", "cv_data", "cv_data_en"]


def test_list_cvs_on_missing_dir(tmp_path):
    assert list_cvs(tmp_path / "nope") == []


def test_cvs_endpoint_lists_existing(client):
    d = client.get("/api/cvs").json()
    assert d["default_cv"] == "cv_data"
    assert d["cvs"] == ["cv_data"]


def test_create_cv_writes_starter_file(client, data_dir):
    r = client.post("/api/cvs", json={"name": "acme"})
    assert r.status_code == 200
    assert r.json()["name"] == "acme"
    assert (data_dir / "acme.yaml").exists()
    r = client.get("/api/yaml", params={"cv": "acme"})
    assert r.status_code == 200
    assert "Your Name" in r.json()["yaml"]


def test_new_cv_starts_in_english(client, data_dir):
    """English is the default; a CV changes it by editing meta.lang."""
    client.post("/api/cvs", json={"name": "acme"})
    starter = (data_dir / "acme.yaml").read_text(encoding="utf-8")
    assert "lang: en" in starter
    r = client.post("/api/preview", json={"yaml": starter})
    assert "Work Experience" in r.text


def test_create_cv_duplicates_the_source(client, data_dir):
    r = client.post("/api/cvs", json={"name": "acme", "copy_from": "cv_data"})
    assert r.status_code == 200
    assert (data_dir / "acme.yaml").read_text(encoding="utf-8") == SAMPLE_YAML
    assert (data_dir / "cv_data.yaml").read_text(encoding="utf-8") == SAMPLE_YAML


def test_create_cv_rejects_duplicate_name(client):
    assert client.post("/api/cvs", json={"name": "cv_data"}).status_code == 409


def test_create_cv_rejects_unknown_source(client):
    r = client.post("/api/cvs", json={"name": "acme", "copy_from": "ghost"})
    assert r.status_code == 404


@pytest.mark.parametrize("name", ["../escape", "sub/dir", "with space"])
def test_create_cv_rejects_unsafe_names(client, name, data_dir):
    r = client.post("/api/cvs", json={"name": name})
    assert r.status_code in (400, 404, 422)
    assert list(data_dir.glob("*.yaml")) == [data_dir / "cv_data.yaml"]


def test_save_and_read_are_scoped_to_the_cv(client, data_dir):
    client.post("/api/cvs", json={"name": "acme"})
    r = client.post("/api/save", json={"yaml": "name: Acme Version", "cv": "acme"})
    assert r.status_code == 200
    assert "Acme Version" in (data_dir / "acme.yaml").read_text(encoding="utf-8")
    # The other CV is untouched.
    assert (data_dir / "cv_data.yaml").read_text(encoding="utf-8") == SAMPLE_YAML
    assert "Acme Version" in client.get(
        "/api/yaml", params={"cv": "acme"}
    ).json()["yaml"]


def test_two_cvs_can_be_in_different_languages(client, data_dir):
    """The former _en variant is now simply a second CV."""
    english = SAMPLE_YAML.replace("  lang: es", "  lang: en")
    client.post("/api/save", json={"yaml": english, "cv": "cv_data_en"})
    assert client.get("/api/cvs").json()["cvs"] == ["cv_data", "cv_data_en"]

    es = client.get("/api/yaml", params={"cv": "cv_data"}).json()["yaml"]
    en = client.get("/api/yaml", params={"cv": "cv_data_en"}).json()["yaml"]
    assert "Experiencia Laboral" in client.post("/api/preview", json={"yaml": es}).text
    assert "Work Experience" in client.post("/api/preview", json={"yaml": en}).text


def test_delete_cv_backs_up_then_removes(client, data_dir):
    client.post("/api/cvs", json={"name": "acme"})
    r = client.delete("/api/cvs/acme")
    assert r.status_code == 200
    assert r.json()["removed"] == ["acme.yaml"]
    assert not (data_dir / "acme.yaml").exists()
    assert (data_dir / "acme.yaml.bak").exists()  # recoverable


def test_delete_refuses_the_startup_cv(client, data_dir):
    r = client.delete("/api/cvs/cv_data")
    assert r.status_code == 400
    assert (data_dir / "cv_data.yaml").exists()


def test_delete_unknown_cv_returns_404(client):
    assert client.delete("/api/cvs/ghost").status_code == 404


def test_server_started_on_any_file_uses_that_file_as_default_cv(data_dir):
    (data_dir / "cv_data_en.yaml").write_text("name: English", encoding="utf-8")
    c = TestClient(create_app(data_dir / "cv_data_en.yaml"))
    assert c.get("/api/config").json()["default_cv"] == "cv_data_en"
    assert "English" in c.get("/api/yaml").json()["yaml"]


def test_editor_page_and_static_assets_served(client):
    assert "CV Maker" in client.get("/").text
    r = client.get("/static/js-yaml.min.js")
    assert r.status_code == 200
    assert "jsyaml" in r.text[:300]


def test_linkify_escapes_and_links():
    result = str(linkify("a <b> & [x](https://e.com) y"))
    assert '<a href="https://e.com">x</a>' in result
    assert "&lt;b&gt;" in result
    assert "&amp;" in result


# --- ATS-safe rendering ------------------------------------------------------


def _extract(pdf_bytes: bytes) -> str:
    """Read a PDF back the way an ATS parser does."""
    import io

    from pdfminer.high_level import extract_text

    return extract_text(io.BytesIO(pdf_bytes))


def test_section_headings_survive_pdf_extraction(client):
    """Letter-spaced headings extract character by character under pdfminer,
    which is the extraction layer beneath most parsers. A parser that segments
    by heading name then finds no sections at all."""
    english = SAMPLE_YAML.replace("  lang: es", "  lang: en")
    pdf = client.post("/api/pdf", json={"yaml": english}).content
    text = _extract(pdf)
    for heading in ("PROFESSIONAL SUMMARY", "WORK EXPERIENCE", "EDUCATION", "SKILLS"):
        assert heading in text, f"{heading!r} did not survive extraction"


def test_contact_links_extract_as_full_urls(client):
    """A bare handle gives a parser nothing to file under a profile field."""
    text = _extract(client.post("/api/pdf", json={"yaml": SAMPLE_YAML}).content)
    assert "linkedin.com/in/jane-doe" in text
    assert "github.com/janedoe" in text


def test_phone_is_rendered_when_present(client):
    body = SAMPLE_YAML.replace(
        "  email: jane@example.com", "  email: jane@example.com\n  phone: +1 555 010 0134"
    )
    assert "+1 555 010 0134" in _extract(client.post("/api/pdf", json={"yaml": body}).content)


def test_summary_is_not_justified(client):
    """Justification pads lines with space runs that break phrase matching."""
    import re

    long_summary = "Machine learning engineer. " * 20
    body = SAMPLE_YAML.replace(
        "summary: A summary with a [link](https://example.com) in it.",
        f"summary: {long_summary}",
    )
    text = _extract(client.post("/api/pdf", json={"yaml": body}).content)
    assert not re.search(r"\S {3,}\S", text), "justification artifacts in extracted text"


def test_display_url_strips_scheme():
    from cv_maker.generator import display_url

    assert display_url("https://linkedin.com/in/x") == "linkedin.com/in/x"
    assert display_url("http://www.github.com/y/") == "github.com/y"
    assert display_url("") == ""


def test_contact_handle_normalises_both_shapes():
    from cv_maker.generator import contact_handle

    base = "https://github.com/"
    assert contact_handle("alafca", base) == {
        "text": "github.com/alafca",
        "url": "https://github.com/alafca",
    }
    assert contact_handle({"text": "alafca"}, base)["text"] == "github.com/alafca"
    assert contact_handle({"url": "https://x.dev/me"}, base)["text"] == "x.dev/me"


# --- ATS endpoint ------------------------------------------------------------


def test_ats_endpoint_returns_a_report(client):
    r = client.post("/api/ats", json={"yaml": SAMPLE_YAML})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"score", "findings", "counts"}
    assert 0 <= body["score"] <= 100
    assert any(f["check"] == "headings" for f in body["findings"])


def test_ats_endpoint_accepts_a_job_description(client):
    r = client.post(
        "/api/ats",
        json={"yaml": SAMPLE_YAML, "job_description": "Kubernetes and Terraform needed."},
    )
    keywords = [f for f in r.json()["findings"] if f["check"] == "keywords"]
    assert keywords and "kubernetes" in keywords[0]["detail"]


def test_ats_endpoint_without_a_job_description_skips_keywords(client):
    r = client.post("/api/ats", json={"yaml": SAMPLE_YAML})
    assert not [f for f in r.json()["findings"] if f["check"] == "keywords"]


def test_ats_endpoint_rejects_invalid_yaml(client):
    assert client.post("/api/ats", json={"yaml": "a: [unclosed"}).status_code == 400


def test_ats_endpoint_reports_a_declared_photo_only_when_rendered(client):
    off = client.post("/api/ats", json={"yaml": SAMPLE_YAML}).json()
    assert any(f["check"] == "photo" for f in off["findings"])


# --- Styles ------------------------------------------------------------------


def test_cv_style_defaults_to_classic_and_ignores_unknown():
    from cv_maker.generator import DEFAULT_STYLE, cv_style

    assert cv_style({}) == DEFAULT_STYLE
    assert cv_style({"meta": None}) == DEFAULT_STYLE
    assert cv_style({"meta": {"style": "cambria"}}) == "cambria"
    # A CV written against a newer tool must still render, not fail.
    assert cv_style({"meta": {"style": "no-such-style"}}) == DEFAULT_STYLE


def test_every_registered_style_has_a_stylesheet():
    from cv_maker.generator import DEFAULT_STYLE, STYLES, style_css

    assert style_css(DEFAULT_STYLE) == ""
    for style in STYLES:
        if style != DEFAULT_STYLE:
            assert "font" in style_css(style)


def test_config_lists_the_styles(client):
    from cv_maker.generator import STYLES

    r = client.get("/api/config")
    styles = r.json()["styles"]
    assert [s["id"] for s in styles] == list(STYLES)
    assert all(s["label"] and s["detail"] for s in styles)


def test_preview_applies_the_style_named_in_meta(client):
    classic = client.post("/api/preview", json={"yaml": SAMPLE_YAML}).text
    assert "Cambria" not in classic

    styled = SAMPLE_YAML.replace("  lang: es\n", "  lang: es\n  style: cambria\n")
    r = client.post("/api/preview", json={"yaml": styled})
    assert r.status_code == 200
    assert "Cambria" in r.text
    assert "Experiencia Laboral" in r.text  # the rest of meta still applies


def test_unknown_style_renders_as_classic(client):
    styled = SAMPLE_YAML.replace("  lang: es\n", "  lang: es\n  style: comic-sans\n")
    r = client.post("/api/preview", json={"yaml": styled})
    assert r.status_code == 200
    assert "Jane Doe" in r.text
    assert "Cambria" not in r.text


def test_languages_render_as_one_line_and_certifications_keep_bullets(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML}).text
    assert '<ul class="languages-line">' in r
    assert "<ul class=\"languages-list\">" in r  # certifications
    assert ".languages-line li {\n    display: inline;" in r


def test_publication_titles_are_not_underlined(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML}).text
    assert ".entry-title a {\n    text-decoration: none;" in r
