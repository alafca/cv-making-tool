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
    data_path_for_lang,
    linkify,
    list_cvs,
    resolve_cv_path,
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
    return TestClient(create_app(data_dir / "cv_data.yaml", default_lang="es"))


def test_config_reports_default_lang(client):
    r = client.get("/api/config")
    assert r.status_code == 200
    assert r.json()["default_lang"] == "es"


def test_read_yaml(client):
    r = client.get("/api/yaml", params={"lang": "es"})
    assert r.status_code == 200
    assert "Jane Doe" in r.json()["yaml"]


def test_read_yaml_missing_lang_falls_back_to_base(client):
    r = client.get("/api/yaml", params={"lang": "en"})
    assert r.status_code == 200
    assert "Jane Doe" in r.json()["yaml"]


def test_preview_renders_html_with_photo_and_links(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML, "lang": "es"})
    assert r.status_code == 200
    assert "<h1" in r.text
    assert "data:image/jpeg;base64" in r.text  # RGBA png converted fine
    assert '<a href="https://example.com/thing">see here</a>' in r.text


def test_preview_escapes_contact_fields(client):
    evil = SAMPLE_YAML.replace("jane@example.com", '"<b>x</b>&y"')
    r = client.post("/api/preview", json={"yaml": evil, "lang": "es"})
    assert "&lt;b&gt;x&lt;/b&gt;&amp;y" in r.text
    assert "<b>x</b>" not in r.text


def test_preview_invalid_yaml_shows_error_page(client):
    r = client.post("/api/preview", json={"yaml": "a: [unclosed", "lang": "es"})
    assert r.status_code == 200
    assert "Invalid YAML" in r.text
    assert "x-page-count" not in r.headers


def test_preview_non_dict_yaml_shows_error_page(client):
    r = client.post("/api/preview", json={"yaml": "just a string", "lang": "es"})
    assert r.status_code == 200
    assert "error" in r.text.lower()


def test_preview_unknown_lang_falls_back_to_english_headers(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML, "lang": "fr"})
    assert r.status_code == 200
    assert "Work Experience" in r.text


def test_preview_reports_page_count(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML, "lang": "es"})
    assert int(r.headers["x-page-count"]) >= 1


def test_pdf_returns_pdf_bytes(client):
    r = client.post("/api/pdf", json={"yaml": SAMPLE_YAML, "lang": "es"})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


def test_pdf_invalid_yaml_returns_400(client):
    r = client.post("/api/pdf", json={"yaml": "a: [unclosed", "lang": "es"})
    assert r.status_code == 400


def test_export_html(client):
    r = client.post("/api/export-html", json={"yaml": SAMPLE_YAML, "lang": "es"})
    assert r.status_code == 200
    assert "<title>Jane Doe - CV</title>" in r.text


def test_save_invalid_yaml_rejected_and_file_untouched(client, data_dir):
    before = (data_dir / "cv_data.yaml").read_text(encoding="utf-8")
    r = client.post("/api/save", json={"yaml": "a: [unclosed", "lang": "es"})
    assert r.status_code == 400
    assert (data_dir / "cv_data.yaml").read_text(encoding="utf-8") == before


def test_save_writes_backup_of_previous_content(client, data_dir):
    r = client.post("/api/save", json={"yaml": "name: New Version", "lang": "es"})
    assert r.status_code == 200
    assert (data_dir / "cv_data.yaml.bak").read_text(encoding="utf-8") == SAMPLE_YAML
    assert "New Version" in (data_dir / "cv_data.yaml").read_text(encoding="utf-8")


def test_save_new_lang_creates_file_without_clobbering_base(client, data_dir):
    r = client.post("/api/save", json={"yaml": "name: English Version", "lang": "en"})
    assert r.status_code == 200
    assert (data_dir / "cv_data_en.yaml").exists()
    assert not (data_dir / "cv_data_en.yaml.bak").exists()  # nothing to back up
    assert (data_dir / "cv_data.yaml").read_text(encoding="utf-8") == SAMPLE_YAML
    # subsequent reads for that lang use the new file
    r = client.get("/api/yaml", params={"lang": "en"})
    assert "English Version" in r.json()["yaml"]


def test_editor_page_is_not_cacheable(client):
    """A stale editor page writes YAML using its own idea of the schema."""
    r = client.get("/")
    assert "no-store" in r.headers.get("cache-control", "")


def test_save_keeps_a_timestamped_history_snapshot(client, data_dir):
    client.post("/api/save", json={"yaml": "name: V2", "lang": "es"})
    snaps = list((data_dir / ".history").glob("cv_data-*.yaml"))
    assert len(snaps) == 1
    assert snaps[0].read_text(encoding="utf-8") == SAMPLE_YAML


def test_two_saves_do_not_destroy_the_original(client, data_dir):
    """The .bak alone loses the good copy after a second save; history must not."""
    client.post("/api/save", json={"yaml": "name: V2", "lang": "es"})
    client.post("/api/save", json={"yaml": "name: V3", "lang": "es"})

    # .bak now only holds V2 -- the original survives only in history.
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
    client.post("/api/save", json={"yaml": "name: V2", "lang": "es"})
    assert client.get("/api/cvs").json()["cvs"] == [
        {"name": "cv_data", "langs": ["es"]}
    ]


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


def test_data_path_for_lang(tmp_path):
    base = tmp_path / "cv.yaml"
    base.write_text("name: x", encoding="utf-8")
    assert data_path_for_lang(base, "es") == base
    assert data_path_for_lang(base, "en") == base  # no _en file yet
    en = tmp_path / "cv_en.yaml"
    en.write_text("name: y", encoding="utf-8")
    assert data_path_for_lang(base, "en") == en


# --- Publications -----------------------------------------------------------


def test_preview_renders_publications(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML, "lang": "en"})
    assert r.status_code == 200
    assert "Publications" in r.text
    assert "A Paper About Testing" in r.text
    assert "Journal of Tests" in r.text
    assert "2024" in r.text
    assert "co-author" in r.text
    # A url turns the title into a link.
    assert '<a href="https://example.com/paper">A Paper About Testing</a>' in r.text


def test_publications_header_is_localised(client):
    r = client.post("/api/preview", json={"yaml": SAMPLE_YAML, "lang": "es"})
    assert "Publicaciones" in r.text


def test_publication_without_url_or_note_still_renders(client):
    body = "name: X\npublications:\n  - title: Bare Paper\n    venue: Some Journal\n    year: 2020\n"
    r = client.post("/api/preview", json={"yaml": body, "lang": "en"})
    assert r.status_code == 200
    assert "Bare Paper" in r.text
    assert "<a href" not in r.text.split("Bare Paper")[0][-200:]


def test_publications_section_absent_when_no_data(client):
    body = "name: X\nsummary: no papers here\n"
    r = client.post("/api/preview", json={"yaml": body, "lang": "en"})
    assert "Publications" not in r.text


def test_publication_fields_are_escaped(client):
    body = 'name: X\npublications:\n  - title: "<script>alert(1)</script>"\n    venue: J\n    year: 2020\n'
    r = client.post("/api/preview", json={"yaml": body, "lang": "en"})
    assert "<script>alert(1)</script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_pdf_includes_publications(client):
    r = client.post("/api/pdf", json={"yaml": SAMPLE_YAML, "lang": "en"})
    assert r.status_code == 200
    assert r.content.startswith(b"%PDF")


# --- Multiple CVs -----------------------------------------------------------


@pytest.mark.parametrize("name", ["acme", "acme-2", "a_b", "X9"])
def test_validate_cv_name_accepts_safe_names(name):
    assert validate_cv_name(name, ("es", "en")) == name


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "sub/dir",
        "sub\\dir",
        "with space",
        "dot.name",
        "",
        "-leading",
        "acme_en",  # would be indistinguishable from a language variant
        "acme_es",
    ],
)
def test_validate_cv_name_rejects_unsafe_or_ambiguous(name):
    with pytest.raises(ValueError):
        validate_cv_name(name, ("es", "en"))


def test_cv_and_resolve_paths(tmp_path):
    base = cv_file_path(tmp_path, "acme", "es", "es")
    assert base == tmp_path / "acme.yaml"
    assert cv_file_path(tmp_path, "acme", "en", "es") == tmp_path / "acme_en.yaml"

    base.write_text("name: x", encoding="utf-8")
    # No English file yet: fall back to the CV's base file.
    assert resolve_cv_path(tmp_path, "acme", "en", "es") == base
    en = tmp_path / "acme_en.yaml"
    en.write_text("name: y", encoding="utf-8")
    assert resolve_cv_path(tmp_path, "acme", "en", "es") == en


def test_list_cvs_groups_languages_and_skips_strays(tmp_path):
    for fname in ["cv_data.yaml", "cv_data_en.yaml", "acme.yaml", "only_en.yaml"]:
        (tmp_path / fname).write_text("name: x", encoding="utf-8")
    (tmp_path / "not a cv.yaml").write_text("name: x", encoding="utf-8")
    (tmp_path / "cv_data.yaml.bak").write_text("name: old", encoding="utf-8")

    assert list_cvs(tmp_path, ("es", "en"), "es") == [
        {"name": "acme", "langs": ["es"]},
        {"name": "cv_data", "langs": ["en", "es"]},
        {"name": "only", "langs": ["en"]},
    ]


def test_list_cvs_on_missing_dir(tmp_path):
    assert list_cvs(tmp_path / "nope", ("es", "en"), "es") == []


def test_cvs_endpoint_lists_existing(client):
    r = client.get("/api/cvs")
    assert r.status_code == 200
    d = r.json()
    assert d["default_cv"] == "cv_data"
    assert d["cvs"] == [{"name": "cv_data", "langs": ["es"]}]


def test_create_cv_writes_starter_file(client, data_dir):
    r = client.post("/api/cvs", json={"name": "acme"})
    assert r.status_code == 200
    assert r.json()["name"] == "acme"
    assert (data_dir / "acme.yaml").exists()
    # The starter must be loadable by the editor without further edits.
    r = client.get("/api/yaml", params={"cv": "acme", "lang": "es"})
    assert r.status_code == 200
    assert "Your Name" in r.json()["yaml"]


def test_create_cv_duplicates_all_languages(client, data_dir):
    client.post("/api/save", json={"yaml": "name: English", "lang": "en"})
    r = client.post("/api/cvs", json={"name": "acme", "copy_from": "cv_data"})
    assert r.status_code == 200
    assert r.json()["langs"] == ["en", "es"]
    assert (data_dir / "acme.yaml").read_text(encoding="utf-8") == SAMPLE_YAML
    assert (data_dir / "acme_en.yaml").read_text(encoding="utf-8") == "name: English"


def test_create_cv_rejects_duplicate_name(client):
    assert client.post("/api/cvs", json={"name": "cv_data"}).status_code == 409


def test_create_cv_rejects_unknown_source(client):
    r = client.post("/api/cvs", json={"name": "acme", "copy_from": "ghost"})
    assert r.status_code == 404


@pytest.mark.parametrize("name", ["../escape", "sub/dir", "with space", "acme_en"])
def test_create_cv_rejects_unsafe_names(client, name, data_dir):
    r = client.post("/api/cvs", json={"name": name})
    assert r.status_code in (400, 404, 422)
    assert list(data_dir.glob("*.yaml")) == [data_dir / "cv_data.yaml"]


def test_save_and_read_are_scoped_to_the_cv(client, data_dir):
    client.post("/api/cvs", json={"name": "acme"})
    r = client.post("/api/save", json={"yaml": "name: Acme Version", "lang": "es", "cv": "acme"})
    assert r.status_code == 200
    assert "Acme Version" in (data_dir / "acme.yaml").read_text(encoding="utf-8")
    # The other CV is untouched.
    assert (data_dir / "cv_data.yaml").read_text(encoding="utf-8") == SAMPLE_YAML
    assert "Acme Version" in client.get(
        "/api/yaml", params={"cv": "acme", "lang": "es"}
    ).json()["yaml"]


def test_save_to_new_cv_lang_creates_suffixed_file(client, data_dir):
    client.post("/api/cvs", json={"name": "acme"})
    r = client.post("/api/save", json={"yaml": "name: Acme EN", "lang": "en", "cv": "acme"})
    assert r.status_code == 200
    assert (data_dir / "acme_en.yaml").read_text(encoding="utf-8") == "name: Acme EN"
    assert "Your Name" in (data_dir / "acme.yaml").read_text(encoding="utf-8")


def test_read_unknown_cv_returns_404(client):
    r = client.get("/api/yaml", params={"cv": "ghost", "lang": "es"})
    assert r.status_code == 404


def test_read_rejects_path_traversal(client, tmp_path):
    secret = tmp_path.parent / "secret.yaml"
    secret.write_text("name: secret", encoding="utf-8")
    r = client.get("/api/yaml", params={"cv": "../secret", "lang": "es"})
    assert r.status_code == 400
    assert "Invalid CV name" in r.json()["detail"]


def test_delete_cv_backs_up_then_removes(client, data_dir):
    client.post("/api/cvs", json={"name": "acme"})
    client.post("/api/save", json={"yaml": "name: Acme EN", "lang": "en", "cv": "acme"})

    r = client.delete("/api/cvs/acme")
    assert r.status_code == 200
    assert sorted(r.json()["removed"]) == ["acme.yaml", "acme_en.yaml"]
    assert not (data_dir / "acme.yaml").exists()
    assert not (data_dir / "acme_en.yaml").exists()
    # Recoverable: the content is kept alongside.
    assert (data_dir / "acme_en.yaml.bak").read_text(encoding="utf-8") == "name: Acme EN"


def test_delete_refuses_the_startup_cv(client, data_dir):
    r = client.delete("/api/cvs/cv_data")
    assert r.status_code == 400
    assert (data_dir / "cv_data.yaml").exists()


def test_delete_unknown_cv_returns_404(client):
    assert client.delete("/api/cvs/ghost").status_code == 404


def test_downloads_are_named_after_the_cv(client):
    r = client.post("/api/pdf", json={"yaml": SAMPLE_YAML, "lang": "en", "cv": "acme"})
    assert r.headers["content-disposition"] == "attachment; filename=acme_en.pdf"
    r = client.post("/api/export-html", json={"yaml": SAMPLE_YAML, "lang": "es"})
    assert r.headers["content-disposition"] == "attachment; filename=cv_data_es.html"


@pytest.mark.parametrize("body", ["name: Minimal", "name: Minimal\nmeta:\n"])
def test_render_survives_missing_or_empty_meta(client, body):
    """A hand-written or freshly created CV need not carry a meta: block."""
    r = client.post("/api/preview", json={"yaml": body, "lang": "en"})
    assert r.status_code == 200
    assert "Minimal" in r.text
    assert "error" not in r.text.lower()
    assert client.post("/api/pdf", json={"yaml": body, "lang": "en"}).status_code == 200


def test_opening_in_another_language_keeps_the_disk_layout(data_dir):
    """--lang picks the editor's starting language, not the base file's language.

    Regression: passing initial_lang through as default_lang made the server
    treat the Spanish cv_data.yaml as the English file, hid cv_data_en.yaml
    from the CV list, and served the same content for both languages.
    """
    from fastapi.testclient import TestClient

    (data_dir / "cv_data_en.yaml").write_text("name: English Version", encoding="utf-8")
    c = TestClient(create_app(data_dir / "cv_data.yaml", "es", initial_lang="en"))

    cfg = c.get("/api/config").json()
    assert cfg["default_lang"] == "es"
    assert cfg["initial_lang"] == "en"
    # Both files still resolve to their own content.
    assert "English Version" in c.get("/api/yaml", params={"lang": "en"}).json()["yaml"]
    assert "Jane Doe" in c.get("/api/yaml", params={"lang": "es"}).json()["yaml"]
    # And the English file is still recognised as part of the CV.
    assert c.get("/api/cvs").json()["cvs"] == [
        {"name": "cv_data", "langs": ["en", "es"]}
    ]


def test_initial_lang_defaults_to_base_lang(client):
    assert client.get("/api/config").json()["initial_lang"] == "es"


def test_unknown_initial_lang_falls_back(data_dir):
    from fastapi.testclient import TestClient

    c = TestClient(create_app(data_dir / "cv_data.yaml", "es", initial_lang="fr"))
    assert c.get("/api/config").json()["initial_lang"] == "es"


def test_config_reports_cv_defaults(client):
    d = client.get("/api/config").json()
    assert d["default_cv"] == "cv_data"
    assert set(d["langs"]) == {"es", "en"}


def test_server_started_on_a_lang_variant_uses_the_base_cv_name(data_dir):
    from fastapi.testclient import TestClient

    (data_dir / "cv_data_en.yaml").write_text("name: English", encoding="utf-8")
    c = TestClient(create_app(data_dir / "cv_data_en.yaml", default_lang="es"))
    assert c.get("/api/config").json()["default_cv"] == "cv_data"
