"""Tests for the ATS checks.

Uses synthetic CV data so the suite is independent of personal CV content.
"""
from pathlib import Path

import pytest
import yaml
from PIL import Image

from cv_maker import ats
from cv_maker.ats import (
    CRITICAL,
    GOOD,
    HIGH,
    LOW,
    MEDIUM,
    analyse,
    check_content,
    check_keywords,
    check_structure,
    format_report,
)

GOOD_YAML = """\
name: Jane Doe
headline: AI Engineer
contact:
  email: jane@example.com
  phone: "+1 555 010 0134"
  location: Lakewood Falls, OR
  linkedin:
    text: jane-doe
    url: https://linkedin.com/in/jane-doe
meta:
  lang: en
summary: >-
  Machine Learning (ML) engineer building Natural Language Processing (NLP)
  and Computer Vision (CV) systems, with Large Language Model (LLM) and
  Retrieval-Augmented Generation (RAG) work in Artificial Intelligence (AI).
experience:
  - title: Engineer
    company: ACME
    location: Remote
    dates: Jan 2020 - Present
    bullets:
      - Built a pipeline processing 40,000 documents, cutting review time by 60%.
      - Led a rebuild that raised accuracy from 78% to 89% across 12 sites.
education:
  - institution: Test University
    degree: BSc Testing
    dates: 2016 - 2020
skills:
  - category: Tools
    entries: Python, SQL, Docker
"""


def load(text: str) -> dict:
    return yaml.safe_load(text)


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    Image.new("RGB", (20, 20), (10, 10, 10)).save(tmp_path / "photo.png")
    return tmp_path


def severities(findings, check: str) -> list[str]:
    return [f.severity for f in findings if f.check == check]


# --- parse-back --------------------------------------------------------------


def test_clean_cv_extracts_with_no_critical_findings(data_dir):
    report = analyse(load(GOOD_YAML), data_dir)
    assert not [f for f in report.findings if f.severity == CRITICAL]
    assert GOOD in severities(report.findings, "headings")
    assert GOOD in severities(report.findings, "reading-order")
    assert report.score >= 80


def test_letter_spacing_is_caught_as_critical(data_dir, monkeypatch):
    """The regression that started all this: tracked headings extract as
    'W O R K   E X P E R I E N C E' and no parser can segment the document."""
    real_render = ats.render_html

    def tracked(data, show_photo, yaml_dir, primary_color=None):
        html = real_render(data, show_photo, yaml_dir, primary_color)
        return html.replace("h2 {", "h2 { letter-spacing: 2pt;", 1)

    monkeypatch.setattr(ats, "render_html", tracked)
    report = analyse(load(GOOD_YAML), data_dir)

    assert CRITICAL in severities(report.findings, "headings")
    assert HIGH in severities(report.findings, "tracking")
    assert report.score < 60


def test_justified_text_is_reported(data_dir, monkeypatch):
    real_render = ats.render_html

    def justified(data, show_photo, yaml_dir, primary_color=None):
        html = real_render(data, show_photo, yaml_dir, primary_color)
        return html.replace(".summary p {", ".summary p { text-align: justify;", 1)

    monkeypatch.setattr(ats, "render_html", justified)
    # Varied word lengths on purpose: justification only stretches the spaces
    # when a line does not happen to fill evenly.
    data = load(GOOD_YAML)
    data["summary"] = (
        "Engineer delivering production artificial intelligence systems for "
        "regulated organisations, from initial client conversation through "
        "architecture, implementation, deployment and ongoing observability, "
        "with particular emphasis on evaluation methodology and reproducible "
        "experimentation across heterogeneous and frequently incomplete data."
    )
    report = analyse(data, data_dir)
    assert MEDIUM in severities(report.findings, "spacing")


def test_extracted_text_is_returned_for_inspection(data_dir):
    report = analyse(load(GOOD_YAML), data_dir)
    assert "WORK EXPERIENCE" in report.extracted_text
    assert "linkedin.com/in/jane-doe" in report.extracted_text


# --- structure ---------------------------------------------------------------


def test_missing_phone_is_high():
    data = load(GOOD_YAML)
    del data["contact"]["phone"]
    assert HIGH in severities(check_structure(data, 1, "en"), "phone")


def test_present_phone_passes():
    assert not severities(check_structure(load(GOOD_YAML), 1, "en"), "phone")


def test_malformed_phone_is_flagged():
    data = load(GOOD_YAML)
    data["contact"]["phone"] = "call me"
    assert MEDIUM in severities(check_structure(data, 1, "en"), "phone")


def test_availability_text_in_location_is_flagged():
    data = load(GOOD_YAML)
    data["contact"]["location"] = "Lakewood Falls, OR | Available November 2026"
    findings = check_structure(data, 1, "en")
    assert MEDIUM in severities(findings, "location")


def test_missing_email_is_critical():
    data = load(GOOD_YAML)
    del data["contact"]["email"]
    assert CRITICAL in severities(check_structure(data, 1, "en"), "email")


def test_duration_inside_date_field_is_flagged():
    data = load(GOOD_YAML)
    data["experience"][0]["dates"] = "Nov 2025 - Present (10+ months)"
    assert LOW in severities(check_structure(data, 1, "en"), "dates")


def test_dates_without_a_year_are_flagged():
    data = load(GOOD_YAML)
    data["experience"][0]["dates"] = "recently"
    assert MEDIUM in severities(check_structure(data, 1, "en"), "dates")


def test_unexpanded_acronyms_are_flagged():
    data = load(GOOD_YAML)
    data["summary"] = "Engineer working on ML, NLP and RAG systems."
    findings = check_structure(data, 1, "en")
    assert MEDIUM in severities(findings, "acronyms")
    detail = next(f.detail for f in findings if f.check == "acronyms")
    assert "Machine Learning" in detail


def test_expanded_acronyms_pass():
    assert not severities(check_structure(load(GOOD_YAML), 1, "en"), "acronyms")


def test_three_pages_is_flagged_and_two_is_not():
    data = load(GOOD_YAML)
    assert MEDIUM in severities(check_structure(data, 3, "en"), "length")
    assert severities(check_structure(data, 2, "en"), "length") == [GOOD]


def test_declared_photo_is_a_low_note():
    data = load(GOOD_YAML)
    data["meta"]["photo"] = "photo.png"
    assert LOW in severities(check_structure(data, 1, "en"), "photo")


# --- content -----------------------------------------------------------------


def test_bullets_with_metrics_pass():
    assert severities(check_content(load(GOOD_YAML)), "metrics") == [GOOD]


def test_bullets_without_metrics_are_flagged():
    data = load(GOOD_YAML)
    data["experience"][0]["bullets"] = [
        "Built a thing for a client.",
        "Led the development of another thing.",
        "Deployed something useful.",
    ]
    assert MEDIUM in severities(check_content(data), "metrics")


def test_weak_openings_are_flagged():
    data = load(GOOD_YAML)
    data["experience"][0]["bullets"] = ["Development of a data pipeline for reporting."]
    assert MEDIUM in severities(check_content(data), "verbs")


def test_overlong_bullets_are_flagged():
    data = load(GOOD_YAML)
    data["experience"][0]["bullets"] = ["Built " + "x" * 400]
    assert LOW in severities(check_content(data), "bullet-length")


def test_content_checks_are_silent_without_experience():
    assert check_content({"name": "X"}) == []


# --- keywords ----------------------------------------------------------------


def test_keyword_overlap_reports_missing_terms():
    findings = check_keywords(
        load(GOOD_YAML), "We need Python, Kubernetes, Terraform and MLflow."
    )
    detail = findings[0].detail
    assert "kubernetes" in detail
    assert "terraform" in detail
    assert "python" not in detail  # the CV has it


def test_keyword_phrases_do_not_cross_punctuation():
    """In "SQL, AWS" the two are separate skills, not the phrase "sql aws"."""
    phrases = ats._phrases("SQL, AWS, machine learning")
    assert "machine learning" in phrases
    assert "sql aws" not in phrases


def test_multiword_terms_are_reported_whole():
    findings = check_keywords(load(GOOD_YAML), "Experience with vector database design.")
    detail = findings[0].detail
    assert "vector database" in detail
    # The phrase subsumes its parts, so they are not also listed separately.
    assert ", database" not in detail


def test_tokens_drop_trailing_punctuation():
    assert "learning" in ats._tokens("deep learning.")
    assert "learning." not in ats._tokens("deep learning.")


def test_node_js_and_cpp_survive_tokenisation():
    tokens = ats._tokens("We use Node.js and C++ daily")
    assert "node.js" in tokens
    assert "c++" in tokens


def test_no_job_description_yields_no_keyword_findings():
    assert check_keywords(load(GOOD_YAML), "") == []
    assert check_keywords(load(GOOD_YAML), "   ") == []


# --- report ------------------------------------------------------------------


def test_score_drops_with_severity():
    clean = ats.Report(findings=[], score=100)
    assert clean.counts()[CRITICAL] == 0
    report = ats.Report(
        findings=[ats.Finding(CRITICAL, "x", "bad"), ats.Finding(LOW, "y", "meh")]
    )
    assert report.counts() == {CRITICAL: 1, HIGH: 0, MEDIUM: 0, LOW: 1, GOOD: 0}


def test_format_report_is_plain_text(data_dir):
    text = format_report(analyse(load(GOOD_YAML), data_dir))
    assert "ATS score:" in text
    assert "headings" in text


def test_report_serialises_for_the_api(data_dir):
    payload = analyse(load(GOOD_YAML), data_dir).as_dict()
    assert set(payload) == {"score", "findings", "counts"}
    assert all(set(f) == {"severity", "check", "message", "detail"} for f in payload["findings"])
