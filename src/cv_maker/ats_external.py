"""Second opinions on the rendered CV from open-source ATS tools.

The checks in ats.py are one opinion, and a home-grown one. Real applicant
tracking systems run parsers written by other people with other blind spots,
so two more are run over the same PDF and reported next to ours. They are
never blended into our score: each one is shown on its own, with its own
caveats, so a low number can be traced to what that tool actually saw.

- lever: pyresume (PyPI "leverparser"), a parser that approximates Lever's
  ATS. It has no score of its own. Because this app owns the source YAML, we
  can measure what no black-box checker can: how much of the real content the
  parser recovered. That recall is the "parse fidelity" score.

- scorer: ats-resume-scorer (PyPI), a rules-based scorer with seven weighted
  categories and a letter grade. Three of those categories score against a
  job description, so it only runs when one is given. It pins an old numpy
  with no wheel for Python 3.13, so it runs in its own Python 3.12
  environment through uv, as a subprocess. Its own parser is crude, which is
  the point: the score is what a weak parser makes of the page.

CV_MAKER_EXTERNAL_ATS selects which run: "lever" (default), "all" or "off".
The scorer is opt-in because its first run installs about 400 MB; the test
suite sets "off" so a check never spawns that install.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path

LEVER_ID = "lever"
SCORER_ID = "scorer"

LEVER_LABEL = "Lever-style parse"
SCORER_LABEL = "ats-resume-scorer"

# Pinned so a check today and a check next year measure the same thing.
SCORER_PACKAGE = "ats-resume-scorer==2.0.0"
SCORER_PYTHON = "3.12"
SPACY_MODEL = (
    "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/"
    "en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
)
# The first run installs the scorer and its spaCy model; later runs load
# spaCy, which is most of the ten seconds they take.
SCORER_TIMEOUT_S = 900

_RUNNER = Path(__file__).parent / "_scorer_runner.py"


@dataclass
class ExternalScore:
    id: str
    label: str
    score: int | None
    available: bool
    # Why there is no score, or how to read the one there is.
    note: str = ""
    breakdown: dict = field(default_factory=dict)
    # What the tool would tell the candidate to fix, in its own words.
    findings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "score": self.score,
            "available": self.available,
            "note": self.note,
            "breakdown": self.breakdown,
            "findings": self.findings,
        }


def mode() -> str:
    value = (os.environ.get("CV_MAKER_EXTERNAL_ATS") or "lever").strip().lower()
    return value if value in {"all", "lever", "off"} else "lever"


def run_external(data: dict, pdf_bytes: bytes, job_description: str = "") -> list[ExternalScore]:
    """Every second opinion, in display order. Never raises: a tool that fails
    reports itself unavailable with the reason."""
    which = mode()
    if which == "off":
        return [
            ExternalScore(LEVER_ID, LEVER_LABEL, None, False, "Disabled by CV_MAKER_EXTERNAL_ATS=off."),
            ExternalScore(SCORER_ID, SCORER_LABEL, None, False, "Disabled by CV_MAKER_EXTERNAL_ATS=off."),
        ]
    out = [lever_fidelity(data, pdf_bytes)]
    if which == "lever":
        out.append(ExternalScore(
            SCORER_ID, SCORER_LABEL, None, False,
            "Off by default: its first run installs about 400 MB. "
            "Set CV_MAKER_EXTERNAL_ATS=all to turn it on.",
        ))
    else:
        out.append(scorer_report(pdf_bytes, job_description))
    return out


# --- lever: parse fidelity ----------------------------------------------------

# What each recovered item is worth. Contact details and job titles are what a
# recruiter searches on, so they weigh more than a language or the summary.
_W_NAME = 3
_W_EMAIL = 2
_W_PHONE = 2
_W_LOCATION = 1
_W_PROFILE = 1
_W_TITLE = 2
_W_COMPANY = 2
_W_INSTITUTION = 2
_W_SKILLS = 4
_W_LANGUAGES = 1
_W_SUMMARY = 1


def _norm(text) -> str:
    """Lower-case ASCII with punctuation collapsed, for forgiving matching."""
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _contains(haystacks, needle) -> bool:
    n = _norm(needle)
    if not n:
        return False
    return any(n in _norm(h) for h in haystacks if h)


def _url_handle(value) -> str:
    """The last path segment of a profile URL or handle, which is what
    survives when a parser truncates the scheme or the host."""
    if isinstance(value, dict):
        value = value.get("url") or value.get("text") or ""
    return _norm(str(value or "").rstrip("/").rsplit("/", 1)[-1])


def parse_with_lever(pdf_bytes: bytes) -> dict:
    """pyresume's structured view of the PDF, as a plain dict."""
    from pyresume import ResumeParser

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as handle:
        handle.write(pdf_bytes)
        path = handle.name
    try:
        return ResumeParser().parse(path).to_dict()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def fidelity(data: dict, parsed: dict) -> ExternalScore:
    """How much of the YAML the parser got back, weighted by what matters."""
    earned = 0.0
    total = 0.0
    lost: list[str] = []

    def item(weight: float, recovered: bool, label: str) -> None:
        nonlocal earned, total
        total += weight
        if recovered:
            earned += weight
        else:
            lost.append(label)

    contact = data.get("contact") or {}
    info = parsed.get("contact_info") or {}

    if data.get("name"):
        item(_W_NAME, _norm(info.get("name")) == _norm(data["name"]), "name")
    if contact.get("email"):
        item(_W_EMAIL, _norm(info.get("email")) == _norm(contact["email"]), "email")
    if contact.get("phone"):
        digits = re.sub(r"\D", "", str(info.get("phone") or ""))
        item(_W_PHONE, bool(digits) and digits in re.sub(r"\D", "", str(contact["phone"])), "phone")
    if contact.get("location"):
        item(_W_LOCATION, _contains([info.get("address"), info.get("location")], contact["location"]), "location")
    for key in ("linkedin", "github"):
        if contact.get(key):
            handle = _url_handle(contact[key])
            found = handle and handle in _norm(info.get(key) or "")
            item(_W_PROFILE, bool(found), f"{key} profile")

    jobs = parsed.get("experience") or []
    titles = [j.get("title") for j in jobs]
    companies = [j.get("company") for j in jobs]
    for job in data.get("experience") or []:
        if job.get("title"):
            item(_W_TITLE, _contains(titles, job["title"]), f"job title '{job['title']}'")
        if job.get("company"):
            # A parser may file the company under the title line, or vice versa.
            item(_W_COMPANY, _contains(companies + titles, job["company"]), f"employer '{job['company']}'")

    schools = [e.get("institution") for e in parsed.get("education") or []]
    schools += [e.get("degree") for e in parsed.get("education") or []]
    for edu in data.get("education") or []:
        if edu.get("institution"):
            # Strip a trailing "(Country)" so a parser that drops it still matches.
            core = re.sub(r"\s*\(.*?\)\s*$", "", str(edu["institution"]))
            item(_W_INSTITUTION, _contains(schools, core), f"institution '{core}'")

    wanted = []
    for row in data.get("skills") or []:
        for entry in str(row.get("entries") or "").split(","):
            entry = re.sub(r"\(.*?\)", "", entry).strip()
            if entry:
                wanted.append(entry)
    if wanted:
        got = [s.get("name") if isinstance(s, dict) else s for s in parsed.get("skills") or []]
        blob = " ".join(_norm(g) for g in got)
        hits = [w for w in wanted if _norm(w) and _norm(w) in blob]
        total += _W_SKILLS
        earned += _W_SKILLS * len(hits) / len(wanted)
        missing = [w for w in wanted if w not in hits]
        if missing:
            lost.append(f"{len(missing)} of {len(wanted)} skills ({', '.join(missing[:6])}{'...' if len(missing) > 6 else ''})")

    langs = [l.get("name") if isinstance(l, dict) else l for l in data.get("languages") or []]
    if langs:
        got = [l.get("name") if isinstance(l, dict) else l for l in parsed.get("languages") or []]
        hits = [l for l in langs if _contains(got, l)]
        total += _W_LANGUAGES
        earned += _W_LANGUAGES * len(hits) / len(langs)
        if len(hits) < len(langs):
            lost.append("languages " + ", ".join(l for l in langs if l not in hits))

    if data.get("summary"):
        item(_W_SUMMARY, bool(parsed.get("summary")), "summary")

    score = round(100 * earned / total) if total else None
    meta = parsed.get("extraction_metadata") or {}
    breakdown = {
        "sections_found": meta.get("sections_found") or [],
        "experience_entries": f"{len(jobs)} parsed, {len(data.get('experience') or [])} in the CV",
        "education_entries": f"{len(parsed.get('education') or [])} parsed, {len(data.get('education') or [])} in the CV",
        "parser_confidence": {
            k: round(float(v), 2) for k, v in (parsed.get("confidence_scores") or {}).items()
        },
    }
    findings = ["Not recovered: " + "; ".join(lost)] if lost else ["Everything checked was recovered."]
    note = (
        "Share of the CV's content a Lever-style parser (pyresume) got back, "
        "weighted towards contact details and job titles."
    )
    return ExternalScore(LEVER_ID, LEVER_LABEL, score, True, note, breakdown, findings)


def lever_fidelity(data: dict, pdf_bytes: bytes) -> ExternalScore:
    try:
        parsed = parse_with_lever(pdf_bytes)
    except Exception as exc:  # a parser crash is a finding, not a failure
        return ExternalScore(LEVER_ID, LEVER_LABEL, None, False, f"pyresume could not parse the PDF: {exc}")
    return fidelity(data, parsed)


# --- scorer: ats-resume-scorer in its own environment -------------------------


def scorer_command(pdf_path: str, job_description_path: str) -> list[str]:
    return [
        "uv", "run", "--no-project", "--quiet",
        "--python", SCORER_PYTHON,
        "--with", SCORER_PACKAGE,
        "--with", SPACY_MODEL,
        "python", str(_RUNNER), pdf_path, job_description_path,
    ]


def scorer_report(pdf_bytes: bytes, job_description: str) -> ExternalScore:
    if not job_description.strip():
        return ExternalScore(
            SCORER_ID, SCORER_LABEL, None, False,
            "Paste a job description to get this score: three of its seven "
            "categories score the CV against one.",
        )
    if shutil.which("uv") is None:
        return ExternalScore(SCORER_ID, SCORER_LABEL, None, False, "uv is not on PATH; it runs the scorer in a Python 3.12 environment.")

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = str(Path(tmp) / "cv.pdf")
        jd_path = str(Path(tmp) / "jd.txt")
        Path(pdf_path).write_bytes(pdf_bytes)
        Path(jd_path).write_text(job_description, encoding="utf-8")
        try:
            proc = subprocess.run(
                scorer_command(pdf_path, jd_path),
                capture_output=True, text=True, timeout=SCORER_TIMEOUT_S,
                encoding="utf-8", errors="replace", check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ExternalScore(SCORER_ID, SCORER_LABEL, None, False, f"ats-resume-scorer did not run: {exc}")

    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-3:]
        return ExternalScore(SCORER_ID, SCORER_LABEL, None, False, "ats-resume-scorer failed: " + " | ".join(tail))
    # The runner prints one JSON line last; anything before it is library noise.
    line = next((l for l in reversed(proc.stdout.splitlines()) if l.startswith("{")), "")
    try:
        result = json.loads(line)
    except json.JSONDecodeError:
        return ExternalScore(SCORER_ID, SCORER_LABEL, None, False, "ats-resume-scorer returned no result.")
    return interpret_scorer(result)


def interpret_scorer(result: dict) -> ExternalScore:
    """Map the scorer's report onto an ExternalScore, saying what its parser saw."""
    summary = result.get("resume_summary") or {}
    seen = (
        f"Its parser found {summary.get('experience_count', '?')} experience and "
        f"{summary.get('education_count', '?')} education entries"
        + ("" if summary.get("has_contact_info") else " and no contact block")
        + "."
    )
    note = (
        f"Grade {result.get('grade', '?')}. Rules-based, seven weighted categories. "
        + seen + " Its job parser only recognises lines written as "
        "'Title | Company | Dates', so on any normal layout the title and "
        "experience categories read zero. Weigh the keyword, format, verb "
        "and readability rows; treat the rest as what a crude parser sees."
    )
    breakdown = {k: round(float(v)) for k, v in (result.get("detailed_breakdown") or {}).items()}
    findings = [str(r) for r in result.get("recommendations") or []]
    score = result.get("overall_score")
    return ExternalScore(
        SCORER_ID, SCORER_LABEL,
        round(float(score)) if score is not None else None,
        True, note, breakdown, findings,
    )


def scorer_environment_ready() -> bool:
    """Whether the scorer's environment is installed, without scoring anything."""
    if shutil.which("uv") is None:
        return False
    try:
        proc = subprocess.run(
            ["uv", "run", "--no-project", "--quiet", "--python", SCORER_PYTHON,
             "--with", SCORER_PACKAGE, "--with", SPACY_MODEL,
             "python", "-c", "import ats_resume_scorer, en_core_web_sm"],
            capture_output=True, text=True, timeout=SCORER_TIMEOUT_S, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0


if __name__ == "__main__":  # pragma: no cover - manual warm-up
    print("installing the scorer environment...", file=sys.stderr)
    print("ready" if scorer_environment_ready() else "failed")
