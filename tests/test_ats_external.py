"""Tests for the second-opinion ATS checkers.

The fidelity formula is tested against hand-built parser output, so it needs
no parser at all. One test runs pyresume for real over the fixture CV, which
is fast and in-process. The scorer test is opt-in (`--external`) because it
installs its own Python environment on first run.
"""
from pathlib import Path

import pytest
import yaml

from cv_maker import ats_external
from cv_maker.ats import analyse, format_report
from cv_maker.ats_external import (
    LEVER_ID,
    SCORER_ID,
    ExternalScore,
    fidelity,
    interpret_scorer,
    run_external,
    scorer_report,
)
from tests.conftest import FIXTURES

CV = {
    "name": "Robin Testerson",
    "contact": {
        "email": "robin@example.com",
        "phone": "+1 555 010 0134",
        "location": "Lakewood Falls, OR",
        "linkedin": {"text": "robin-testerson", "url": "https://linkedin.com/in/robin-testerson"},
        "github": "robintest",
    },
    "summary": "An engineer.",
    "experience": [
        {"title": "Senior AI Engineer", "company": "Testworks"},
        {"title": "Data Scientist", "company": "Numbers Ltd"},
    ],
    "education": [{"institution": "University of Vienna (Austria)", "degree": "MSc"}],
    "skills": [{"category": "Languages", "entries": "Python, SQL, Bash (scripting), R"}],
    "languages": [{"name": "English", "level": "native"}, {"name": "German", "level": "B1"}],
}

PERFECT = {
    "contact_info": {
        "name": "Robin Testerson", "email": "robin@example.com", "phone": "+1 (555) 010-0134",
        "address": "Lakewood Falls, OR", "linkedin": "https://linkedin.com/in/robin-testerson",
        "github": "https://github.com/robintest",
    },
    "summary": "An engineer.",
    "experience": [
        {"title": "Senior AI Engineer Mar 2022 – Present", "company": "Testworks"},
        {"title": "Data Scientist", "company": "Numbers Ltd, Vienna"},
    ],
    "education": [{"institution": "University of Vienna 2018 – 2021", "degree": "MSc"}],
    "skills": [{"name": "Python"}, {"name": "Sql"}, {"name": "Bash"}, {"name": "R"}],
    "languages": ["English", "German"],
    "confidence_scores": {"contact_info": 0.9, "experience": 0.8},
    "extraction_metadata": {"sections_found": ["summary", "experience"]},
}


def test_perfect_parse_scores_100():
    score = fidelity(CV, PERFECT)
    assert score.id == LEVER_ID
    assert score.available
    assert score.score == 100
    assert score.findings == ["Everything checked was recovered."]
    assert score.breakdown["experience_entries"] == "2 parsed, 2 in the CV"
    assert score.breakdown["parser_confidence"] == {"contact_info": 0.9, "experience": 0.8}


def test_lost_items_lower_the_score_and_are_named():
    parsed = {
        **PERFECT,
        "contact_info": {**PERFECT["contact_info"], "phone": None, "github": None},
        "experience": [PERFECT["experience"][0]],  # the second job vanished
        "skills": [{"name": "Python"}],
        "languages": ["English"],
    }
    score = fidelity(CV, parsed)
    assert score.score < 100
    lost = score.findings[0]
    assert lost.startswith("Not recovered: ")
    for needle in ("phone", "github profile", "job title 'Data Scientist'",
                   "employer 'Numbers Ltd'", "3 of 4 skills", "languages German"):
        assert needle in lost, needle
    # Weighted: two contact fields, a title, an employer, most skills and a
    # language are gone, so well under 100 but nowhere near zero.
    assert 40 <= score.score <= 80


def test_matching_is_forgiving_about_case_accents_and_dates():
    parsed = {
        **PERFECT,
        "contact_info": {**PERFECT["contact_info"], "name": "ROBIN TESTERSON"},
        "education": [{"institution": "university of vienna (austria) 2018", "degree": ""}],
    }
    assert fidelity(CV, parsed).score == 100


def test_empty_parse_scores_zero_and_survives():
    score = fidelity(CV, {})
    assert score.score == 0
    assert "name" in score.findings[0]
    assert score.breakdown["experience_entries"] == "0 parsed, 2 in the CV"


def test_cv_with_nothing_to_check_has_no_score():
    assert fidelity({}, PERFECT).score is None


def test_run_external_off_reports_both_tools_as_disabled(monkeypatch):
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "off")
    out = run_external(CV, b"%PDF-1.4", "a job ad")
    assert [o.id for o in out] == [LEVER_ID, SCORER_ID]
    assert all(not o.available and o.score is None for o in out)
    assert all("off" in o.note for o in out)


def test_scorer_needs_a_job_description(monkeypatch):
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "all")
    out = scorer_report(b"%PDF-1.4", "   ")
    assert not out.available
    assert "job description" in out.note


def test_scorer_without_uv_is_unavailable(monkeypatch):
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "all")
    monkeypatch.setattr(ats_external.shutil, "which", lambda name: None)
    out = scorer_report(b"%PDF-1.4", "a job ad")
    assert not out.available
    assert "uv" in out.note


def test_scorer_failure_is_reported_not_raised(monkeypatch):
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "all")
    monkeypatch.setattr(ats_external.shutil, "which", lambda name: "uv")

    class Proc:
        returncode = 1
        stdout = ""
        stderr = "boom\nModuleNotFoundError: no such module"

    monkeypatch.setattr(ats_external.subprocess, "run", lambda *a, **k: Proc())
    out = scorer_report(b"%PDF-1.4", "a job ad")
    assert not out.available
    assert "ModuleNotFoundError" in out.note


def test_interpret_scorer_maps_the_report():
    out = interpret_scorer({
        "overall_score": 47.78, "grade": "F",
        "detailed_breakdown": {"keyword_match": 73.29, "title_match": 0},
        "resume_summary": {"experience_count": 0, "education_count": 0, "has_contact_info": False},
        "recommendations": ["Include a phone number", "Add work experience section"],
    })
    assert out.id == SCORER_ID
    assert out.available
    assert out.score == 48
    assert out.breakdown == {"keyword_match": 73, "title_match": 0}
    assert "Grade F" in out.note
    assert "0 experience" in out.note and "no contact block" in out.note
    assert "Title | Company | Dates" in out.note
    assert out.findings == ["Include a phone number", "Add work experience section"]


def test_scorer_runs_through_a_fake_subprocess(monkeypatch):
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "all")
    monkeypatch.setattr(ats_external.shutil, "which", lambda name: "uv")
    seen = {}

    class Proc:
        returncode = 0
        stderr = ""
        stdout = 'warning: fitz is deprecated\n{"overall_score": 61.2, "grade": "D", "detailed_breakdown": {}, "resume_summary": {}, "recommendations": []}\n'

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        assert Path(cmd[-2]).read_bytes() == b"%PDF-1.4"
        assert Path(cmd[-1]).read_text(encoding="utf-8") == "a job ad"
        return Proc()

    monkeypatch.setattr(ats_external.subprocess, "run", fake_run)
    out = scorer_report(b"%PDF-1.4", "a job ad")
    assert out.available and out.score == 61
    assert seen["cmd"][:3] == ["uv", "run", "--no-project"]
    assert "--python" in seen["cmd"] and "3.12" in seen["cmd"]


def test_report_dict_and_text_carry_the_second_opinions():
    from cv_maker.ats import Report

    report = Report(score=90, external=[
        ExternalScore(LEVER_ID, "Lever-style parse", 72, True, "note", {"k": "v"}, ["Not recovered: phone"]),
        ExternalScore(SCORER_ID, "ats-resume-scorer", None, False, "Paste a job description."),
    ])
    d = report.as_dict()
    assert [e["id"] for e in d["external"]] == [LEVER_ID, SCORER_ID]
    assert d["external"][0]["score"] == 72 and d["external"][1]["score"] is None

    text = format_report(report)
    assert "ATS score (built-in): 90/100" in text
    assert "Lever-style parse: 72/100" in text
    assert "ats-resume-scorer: n/a" in text
    assert "Second opinions" in text
    assert "Not recovered: phone" in text
    assert "Paste a job description." in text


# --- real parsers -------------------------------------------------------------


def test_pyresume_recovers_most_of_the_fixture_cv(monkeypatch, tmp_path: Path):
    """The parser really runs, in-process. The fixture is ATS-clean, so a
    Lever-style parser should get the essentials back."""
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "lever")
    data = yaml.safe_load((FIXTURES / "testing_cv.yaml").read_text(encoding="utf-8"))
    report = analyse(data, tmp_path)

    lever, scorer = report.external
    assert lever.id == LEVER_ID and lever.available
    assert lever.score is not None and lever.score >= 60, (lever.score, lever.findings)
    # pyresume files the "Professional Summary" heading as the name and drops
    # the second job on this fixture. Both are real parser behaviour, and the
    # score is meant to show exactly that, so only the essentials it does get
    # are asserted.
    lost = lever.findings[0]
    for essential in ("email", "phone", "job title 'Senior AI Engineer'", "employer 'Testworks'"):
        assert essential not in lost, lost
    assert scorer.id == SCORER_ID and not scorer.available  # mode "lever"


def test_analyse_can_skip_the_second_opinions(tmp_path: Path):
    data = yaml.safe_load((FIXTURES / "testing_cv.yaml").read_text(encoding="utf-8"))
    assert analyse(data, tmp_path, external=False).external == []


@pytest.mark.external
def test_ats_resume_scorer_really_runs(monkeypatch, tmp_path: Path):
    """Installs the scorer's own environment on first run (slow) and scores
    the fixture against a job ad."""
    monkeypatch.setenv("CV_MAKER_EXTERNAL_ATS", "all")
    data = yaml.safe_load((FIXTURES / "testing_cv.yaml").read_text(encoding="utf-8"))
    report = analyse(data, tmp_path, job_description="Senior AI Engineer with Python, RAG and AWS.")
    scorer = report.external[1]
    assert scorer.available, scorer.note
    assert 0 <= scorer.score <= 100
    assert "keyword_match" in scorer.breakdown
