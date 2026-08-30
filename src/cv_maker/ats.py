"""ATS checks for a rendered CV.

An applicant tracking system does not see the PDF; it sees whatever text it can
pull out of it. So the checks here fall into four groups:

- parse-back: render the real PDF, extract the text the way a parser does, and
  assert the data actually survived. This is the only group that tests reality
  rather than a rule of thumb, and it is what caught letter-spaced headings
  turning into "W O R K   E X P E R I E N C E".
- structural: the fields and conventions parsers expect (phone, written-out
  URLs, standard heading names, sane dates, no photo, length).
- content: whether a human reader will find the bullets worth anything -
  metrics, strong openings.
- keywords: overlap with a specific job description, for tailoring.

Each check yields a Finding. Severity is what the caller ranks and filters by.
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path

from cv_maker.generator import cv_lang, render_html
from cv_maker.i18n import get_headers

CRITICAL = "critical"
HIGH = "high"
MEDIUM = "medium"
LOW = "low"
GOOD = "good"

_SEVERITY_ORDER = {CRITICAL: 0, HIGH: 1, MEDIUM: 2, LOW: 3, GOOD: 4}

# Weight per finding when scoring. GOOD findings cost nothing.
_PENALTY = {CRITICAL: 30, HIGH: 12, MEDIUM: 5, LOW: 2, GOOD: 0}


@dataclass
class Finding:
    severity: str
    check: str
    message: str
    detail: str = ""

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "check": self.check,
            "message": self.message,
            "detail": self.detail,
        }


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    score: int = 100
    extracted_text: str = ""

    def as_dict(self) -> dict:
        return {
            "score": self.score,
            "findings": [f.as_dict() for f in self.findings],
            "counts": self.counts(),
        }

    def counts(self) -> dict:
        out = {s: 0 for s in _SEVERITY_ORDER}
        for f in self.findings:
            out[f.severity] += 1
        return out


# --- helpers ----------------------------------------------------------------

# A "word" for keyword matching: keeps in-word dots/pluses/hyphens so that
# "scikit-learn", "node.js" and "c++" survive tokenisation.
_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.#-]*")

# Phrase boundaries: any punctuation or line break separates two terms, so a
# comma-separated skills list does not invent phrases out of its neighbours.
_SEGMENT_RE = re.compile(r"[,;:()\[\]/|\n.!?—–-]+")

_ACTION_VERBS = {
    "built", "designed", "developed", "led", "shipped", "deployed", "owned",
    "created", "delivered", "migrated", "automated", "reduced", "cut",
    "improved", "scaled", "launched", "implemented", "maintained", "landed",
    "won", "drove", "established", "rebuilt", "handled", "performed",
    "leveraged", "contributed", "benchmarked", "trained", "analysed",
    "analyzed", "prototyped", "architected", "mentored", "guided",
}

# Openings that describe an activity rather than an achievement.
_WEAK_OPENINGS = re.compile(
    r"^(development|deployment|design|creation|maintenance|implementation|"
    r"analysis|responsible for|worked on|helped|assisted|participated)\b",
    re.I,
)

_METRIC_RE = re.compile(
    r"(\d[\d,.]*\s*(%|k\b|m\b|bn\b|x\b|hours?|days?|weeks?|months?|years?|"
    r"users?|clients?|customers?|images?|documents?|records?|tests?|sites?|"
    r"people|engineers?)|"
    r"[€$£]\s?\d|\b\d+\s?(%|x)\b|\bn\s?=\s?\d+|\bAUC\b|\bF1\b|~\d)",
    re.I,
)

_PHONE_RE = re.compile(r"(\+\d[\d\s().-]{7,}|\(\d{3}\)\s?\d{3}[-\s]?\d{4}|\b\d{3}[-.\s]\d{3}[-.\s]\d{4}\b)")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_RE = re.compile(r"\b(?:https?://)?(?:www\.)?[\w-]+\.(?:com|org|net|io|dev|ai|eu|es|de)\b/?\S*")

# Acronyms worth spelling out once: parsers and recruiters both match literally.
_ACRONYMS = {
    "ML": "Machine Learning",
    "NLP": "Natural Language Processing",
    "CV": "Computer Vision",
    "LLM": "Large Language Model",
    "RAG": "Retrieval-Augmented Generation",
    "AI": "Artificial Intelligence",
}

# Headings parsers look for. Anything else risks a section going unsegmented.
_STANDARD_HEADINGS = {
    "professional summary", "summary", "work experience", "experience",
    "education", "skills", "publications", "languages", "certifications",
    "projects", "resumen profesional", "experiencia laboral", "educación",
    "habilidades", "publicaciones", "idiomas", "certificaciones",
}


def _tokens(text: str) -> set[str]:
    """Lowercase words, with trailing punctuation trimmed.

    The token pattern keeps in-word dots and pluses so "node.js" and "c++"
    survive; a word ending a sentence must not become "learning.".
    """
    return {
        word
        for m in _TOKEN_RE.finditer(text)
        if (word := m.group(0).lower().rstrip(".-#"))
    }


def _phrases(text: str) -> set[str]:
    """Adjacent word pairs, so "machine learning" is matched as one term.

    Pairs never cross punctuation: in a comma-separated skills list, "SQL, AWS"
    are two separate skills, not the phrase "sql aws".
    """
    out: set[str] = set()
    for segment in _SEGMENT_RE.split(text):
        words = [
            w for m in _TOKEN_RE.finditer(segment)
            if (w := m.group(0).lower().rstrip(".-#"))
        ]
        out |= {f"{a} {b}" for a, b in zip(words, words[1:])}
    return out


def extract_pdf_text(pdf_bytes: bytes) -> str:
    """The text an ATS would see. Isolated so the dependency has one home."""
    from pdfminer.high_level import extract_text

    return extract_text(io.BytesIO(pdf_bytes))


def cv_plain_text(data: dict) -> str:
    """Every string in the CV data, for keyword and content checks."""
    parts: list[str] = []

    def walk(node):
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)
        elif node is not None:
            parts.append(str(node))

    walk(data)
    return "\n".join(parts)


def all_bullets(data: dict) -> list[str]:
    out = []
    for job in data.get("experience") or []:
        for bullet in job.get("bullets") or []:
            out.append(str(bullet))
    return out


# --- checks -----------------------------------------------------------------


def check_parse_back(data: dict, pdf_bytes: bytes, lang: str) -> tuple[list[Finding], str]:
    """Render-and-read-back: did the content actually survive extraction?"""
    findings: list[Finding] = []
    try:
        text = extract_pdf_text(pdf_bytes)
    except Exception as exc:  # pragma: no cover - depends on the PDF engine
        return [Finding(CRITICAL, "extraction", "The PDF's text could not be extracted at all.", str(exc))], ""

    if not text.strip():
        return [Finding(CRITICAL, "extraction", "The PDF has no extractable text layer.")], text

    flat = re.sub(r"\s+", " ", text)
    headers = get_headers(lang)

    # Headings must come back as whole words, not spaced-out glyphs.
    expected = {
        "summary": data.get("summary"),
        "experience": data.get("experience"),
        "education": data.get("education"),
        "skills": data.get("skills"),
        "publications": data.get("publications"),
    }
    missing = [
        headers[key] for key, present in expected.items()
        if present and headers.get(key) and headers[key].lower() not in flat.lower()
    ]
    if missing:
        findings.append(Finding(
            CRITICAL, "headings",
            "Section headings do not survive text extraction, so a parser cannot "
            "segment the document.",
            "Not found in the extracted text: " + ", ".join(missing)
            + ". Letter-spacing is the usual cause: it emits one positioned glyph "
              "per character.",
        ))
    else:
        findings.append(Finding(GOOD, "headings", "All section headings extract as whole words."))

    # Spaced-out glyph runs anywhere ("W O R K   E X P E R I E N C E").
    if re.search(r"(?:\b[A-Za-z] ){5,}[A-Za-z]\b", text):
        findings.append(Finding(
            HIGH, "tracking",
            "Some text extracts one character at a time.",
            "Letter-spacing or unusual glyph positioning splits words apart.",
        ))

    # Justification pads lines with runs of spaces.
    if re.search(r"\S {3,}\S", text):
        findings.append(Finding(
            MEDIUM, "spacing",
            "Runs of 3+ spaces inside lines break phrase matching.",
            "Usually justified text. 'machine   learning' no longer matches "
            "'machine learning'.",
        ))

    # Contact details must be in the text layer, not only in link hrefs.
    contact = data.get("contact") or {}
    if contact.get("email") and str(contact["email"]) not in flat:
        findings.append(Finding(HIGH, "contact", "The email address does not appear in the extracted text."))
    for key, label in (("linkedin", "LinkedIn"), ("github", "GitHub")):
        value = contact.get(key)
        if not value:
            continue
        handle = value.get("text", "") if isinstance(value, dict) else str(value)
        handle = str(handle).rsplit("/", 1)[-1]
        if handle and handle.lower() not in flat.lower():
            findings.append(Finding(
                MEDIUM, "contact",
                f"The {label} profile does not appear in the extracted text.",
                "A link whose href is set but whose visible text is an icon is "
                "invisible to a parser.",
            ))

    # Every bullet should be findable; a missing one means reading order broke.
    bullets = all_bullets(data)
    lost = []
    for bullet in bullets:
        probe = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", bullet)  # unwrap md links
        probe = re.sub(r"\s+", " ", probe).strip()[:40]
        if probe and probe.lower() not in flat.lower():
            lost.append(probe)
    if lost:
        findings.append(Finding(
            HIGH, "reading-order",
            f"{len(lost)} of {len(bullets)} bullets do not survive extraction intact.",
            "First affected: " + lost[0],
        ))
    elif bullets:
        findings.append(Finding(GOOD, "reading-order", f"All {len(bullets)} bullets extract intact."))

    return findings, text


def check_structure(data: dict, pages: int, lang: str) -> list[Finding]:
    """Fields and conventions a parser expects to find."""
    findings: list[Finding] = []
    contact = data.get("contact") or {}
    text = cv_plain_text(data)

    if not contact.get("phone"):
        findings.append(Finding(
            HIGH, "phone",
            "No phone number.",
            "Most parsers have a phone field; an empty one reads as a failed "
            "parse. Add contact.phone.",
        ))
    elif not _PHONE_RE.search(str(contact["phone"])):
        findings.append(Finding(
            MEDIUM, "phone",
            "The phone number is not in a recognisable format.",
            "Prefer an international format, e.g. +1 555 010 0134.",
        ))

    if not contact.get("email"):
        findings.append(Finding(CRITICAL, "email", "No email address."))
    elif not _EMAIL_RE.fullmatch(str(contact["email"]).strip()):
        findings.append(Finding(HIGH, "email", "The email address does not look valid."))

    if not contact.get("location"):
        findings.append(Finding(
            MEDIUM, "location",
            "No location.",
            "Parsers use it to match the role's location filter.",
        ))

    # Anything beyond city/region in the location lands in the wrong field.
    location = str(contact.get("location") or "")
    if re.search(r"\b(available|relocating|open to|immediately|notice)\b", location, re.I):
        findings.append(Finding(
            MEDIUM, "location",
            "The location field carries availability text.",
            f"{location!r} will likely be filed as a location or job title. "
            "Move availability into the summary.",
        ))
    for key, value in contact.items():
        if key in {"email", "phone", "location", "linkedin", "github"}:
            continue
        if re.search(r"\b(available|relocating)\b", str(value), re.I):
            findings.append(Finding(
                MEDIUM, "contact",
                f"contact.{key} carries availability text that belongs in the summary.",
            ))

    if (data.get("meta") or {}).get("photo"):
        findings.append(Finding(
            LOW, "photo",
            "The data declares a profile photo.",
            "Photos are omitted by default, so this only matters if you render "
            "with --photo. US resumes do not carry one.",
        ))

    if pages > 2:
        findings.append(Finding(
            MEDIUM, "length",
            f"{pages} pages.",
            "Two pages is the practical ceiling below staff level.",
        ))
    elif pages:
        findings.append(Finding(GOOD, "length", f"{pages} page{'s' if pages > 1 else ''}."))

    # Dates: a parser regexes these, and a duration in the same field is noise.
    for job in data.get("experience") or []:
        dates = str(job.get("dates") or "")
        if not dates:
            findings.append(Finding(
                HIGH, "dates",
                f"{job.get('title', 'A role')} has no dates.",
            ))
            continue
        if re.search(r"\(.*\b(month|year|mes|año)", dates, re.I):
            findings.append(Finding(
                LOW, "dates",
                f"Duration text inside the date field: {dates!r}",
                "A parser reads this field with a date regex; the duration in "
                "brackets is noise it has to discard.",
            ))
        if not re.search(r"\b(19|20)\d{2}\b", dates) and not re.search(r"present|actualidad", dates, re.I):
            findings.append(Finding(
                MEDIUM, "dates", f"No recognisable year in {dates!r}."
            ))

    # Acronyms should appear expanded at least once for literal matching.
    unexpanded = []
    for acronym, expansion in _ACRONYMS.items():
        if re.search(rf"\b{re.escape(acronym)}\b", text) and expansion.lower() not in text.lower():
            unexpanded.append(f"{acronym} ({expansion})")
    if unexpanded:
        findings.append(Finding(
            MEDIUM, "acronyms",
            "Acronyms used without ever being spelled out.",
            "Write the full form once so literal keyword matching finds it: "
            + "; ".join(unexpanded),
        ))

    if not (data.get("skills") or []):
        findings.append(Finding(HIGH, "skills", "No skills section."))
    if not (data.get("experience") or []):
        findings.append(Finding(CRITICAL, "experience", "No work experience section."))

    return findings


def check_content(data: dict) -> list[Finding]:
    """Whether the bullets say anything a reader can weigh."""
    findings: list[Finding] = []
    bullets = all_bullets(data)
    if not bullets:
        return findings

    without_metric = [b for b in bullets if not _METRIC_RE.search(b)]
    ratio = len(without_metric) / len(bullets)
    if ratio > 0.6:
        findings.append(Finding(
            MEDIUM, "metrics",
            f"{len(without_metric)} of {len(bullets)} bullets carry no number.",
            "Scale, rate, duration or volume all count; a defensible "
            "approximation beats none.",
        ))
    elif ratio > 0:
        findings.append(Finding(
            LOW, "metrics",
            f"{len(without_metric)} of {len(bullets)} bullets carry no number.",
        ))
    else:
        findings.append(Finding(GOOD, "metrics", "Every bullet carries a number."))

    weak = [b for b in bullets if _WEAK_OPENINGS.match(b.strip())]
    if weak:
        findings.append(Finding(
            MEDIUM, "verbs",
            f"{len(weak)} bullet(s) open with an activity rather than an action.",
            "First: " + weak[0][:80] + "... Prefer Built / Led / Shipped / Cut.",
        ))

    no_verb = [
        b for b in bullets
        if (m := re.match(r"\s*([A-Za-z]+)", b)) and m.group(1).lower() not in _ACTION_VERBS
        and not _WEAK_OPENINGS.match(b.strip())
    ]
    if len(no_verb) > len(bullets) / 2:
        findings.append(Finding(
            LOW, "verbs",
            f"{len(no_verb)} of {len(bullets)} bullets do not start with a recognised action verb.",
        ))

    long_bullets = [b for b in bullets if len(b) > 320]
    if long_bullets:
        findings.append(Finding(
            LOW, "bullet-length",
            f"{len(long_bullets)} bullet(s) run over 320 characters.",
            "Long bullets bury their outcome; the reader skims to the next one.",
        ))

    return findings


def check_keywords(data: dict, job_description: str, limit: int = 25) -> list[Finding]:
    """Overlap with a specific job description."""
    if not job_description.strip():
        return []

    cv_text = cv_plain_text(data)
    cv_words = _tokens(cv_text) | _phrases(cv_text)
    jd_words = _tokens(job_description)

    # Only terms worth matching on: drop short and generic words.
    stop = {
        "the", "and", "for", "with", "you", "our", "will", "are", "have", "this",
        "that", "from", "your", "their", "who", "all", "can", "not", "but", "its",
        "job", "role", "work", "team", "teams", "years", "experience", "ability",
        "including", "such", "other", "more", "also", "how", "any", "new", "well",
        "across", "within", "into", "must", "should", "would", "may", "them",
        "they", "were", "been", "when", "what", "which", "while", "where", "each",
        "about", "over", "than", "then", "these", "those", "some", "both", "very",
        "company", "position", "candidate", "candidates", "apply", "please",
        "benefits", "salary", "range", "employees", "employee", "business",
        "seek", "seeking", "looking", "strong", "excellent", "good", "great",
        "using", "use", "used", "help", "helps", "make", "makes", "one", "two",
        "plus", "etc", "via", "per", "own", "get", "day", "way", "end", "top",
    }
    jd_terms = {w for w in jd_words if len(w) > 2 and w not in stop}
    if not jd_terms:
        return []

    # Coverage is measured on single terms; phrases only make the missing list
    # readable. Scoring on phrases too would count "machine" and "learning" and
    # "machine learning" as three separate misses.
    matched = jd_terms & cv_words
    missing = jd_terms - cv_words

    # Where the job description used two missing terms adjacently, report the
    # pair as one term: "vector database" reads better than "database, vector".
    pairs = {
        p for p in _phrases(job_description)
        if all(w in missing for w in p.split())
    }
    covered_by_pair = {w for p in pairs for w in p.split()}
    display = sorted(pairs | (missing - covered_by_pair))

    coverage = round(100 * len(matched) / len(jd_terms))
    severity = GOOD if coverage >= 60 else MEDIUM if coverage >= 40 else HIGH
    return [Finding(
        severity, "keywords",
        f"{coverage}% of the job description's distinctive terms appear in the CV "
        f"({len(matched)} of {len(jd_terms)}).",
        "Missing: " + ", ".join(display[:limit]) + ("..." if len(display) > limit else ""),
    )]


# --- entry point ------------------------------------------------------------


def analyse(
    data: dict,
    yaml_dir: Path,
    job_description: str = "",
    show_photo: bool = False,
) -> Report:
    """Render the CV, read it back, and run every check over the result."""
    from weasyprint import HTML

    lang = cv_lang(data)
    html = render_html(data, show_photo, yaml_dir)
    document = HTML(string=html).render()
    pdf_bytes = document.write_pdf()

    findings, text = check_parse_back(data, pdf_bytes, lang)
    findings += check_structure(data, len(document.pages), lang)
    findings += check_content(data)
    findings += check_keywords(data, job_description)

    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.check))
    score = max(0, 100 - sum(_PENALTY[f.severity] for f in findings))
    return Report(findings=findings, score=score, extracted_text=text)


def format_report(report: Report, show_good: bool = True) -> str:
    """The report as plain text, for the terminal."""
    icons = {CRITICAL: "!!", HIGH: "! ", MEDIUM: "~ ", LOW: ". ", GOOD: "ok"}
    lines = [f"ATS score: {report.score}/100", ""]
    counts = report.counts()
    summary = ", ".join(
        f"{counts[s]} {s}" for s in (CRITICAL, HIGH, MEDIUM, LOW) if counts[s]
    )
    lines.append(summary or "No problems found.")
    lines.append("")
    for finding in report.findings:
        if finding.severity == GOOD and not show_good:
            continue
        lines.append(f"[{icons[finding.severity]}] {finding.check}: {finding.message}")
        if finding.detail:
            lines.append(f"       {finding.detail}")
    return "\n".join(lines)
