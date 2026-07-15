"""Endpoint tests for the CV Maker editor server.

Uses synthetic CV data (not the real data/ files) so tests are independent
of personal CV content.
"""
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from cv_maker.generator import data_path_for_lang, linkify
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
