import base64
import io
import re
from collections.abc import Iterable
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape
from PIL import Image
from weasyprint import HTML

from cv_maker.i18n import get_headers

PHOTO_SIZE = 160  # px (renders at 80px on 2x density)

TEMPLATES_DIR = Path(__file__).parent / "templates"

_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')

# A "CV" is one tailored version of the resume, identified by the stem of its
# data file: <name>.yaml holds the default language, <name>_<lang>.yaml the
# others. The tool started with a single CV called cv_data, which stays the
# default so existing data directories keep working untouched.
DEFAULT_CV = "cv_data"

# Conservative on purpose: the name reaches the filesystem, so anything that
# could escape the data directory (dots, separators) must not get through.
_CV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


def linkify(text):
    """Convert [text](url) to <a> tags, escaping everything else."""
    escaped = str(escape(text))
    result = _LINK_RE.sub(r'<a href="\2">\1</a>', escaped)
    return Markup(result)


def load_data(yaml_path: Path) -> dict:
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data:
        raise ValueError(f"Empty or invalid YAML: {yaml_path}")
    return data


def data_path_for_lang(yaml_path: Path, lang: str, default_lang: str = "es") -> Path:
    """Return the language-specific variant (stem_lang.suffix) if it exists."""
    if lang == default_lang:
        return yaml_path
    candidate = yaml_path.with_name(f"{yaml_path.stem}_{lang}{yaml_path.suffix}")
    return candidate if candidate.exists() else yaml_path


def validate_cv_name(name: str, known_langs: Iterable[str] = ()) -> str:
    """Return name unchanged if it is usable as a CV identifier, else raise.

    Rejects anything that could escape the data directory, plus names ending in
    a known language suffix: "profile_en" would be indistinguishable from the
    English variant of a CV called "profile".
    """
    if not isinstance(name, str) or not _CV_NAME_RE.match(name):
        raise ValueError(
            f"Invalid CV name: {name!r}. Use letters, digits, hyphens and "
            "underscores (max 64 characters, starting with a letter or digit)."
        )
    for lang in known_langs:
        if name.endswith(f"_{lang}"):
            raise ValueError(
                f"Invalid CV name: {name!r} ends in the language suffix "
                f"'_{lang}', which would clash with a language variant."
            )
    return name


def cv_file_path(
    data_dir: Path,
    cv: str,
    lang: str,
    default_lang: str = "es",
    suffix: str = ".yaml",
) -> Path:
    """Exact file for (cv, lang). No fallback, no existence check."""
    stem = cv if lang == default_lang else f"{cv}_{lang}"
    return data_dir / f"{stem}{suffix}"


def resolve_cv_path(
    data_dir: Path,
    cv: str,
    lang: str,
    default_lang: str = "es",
    suffix: str = ".yaml",
) -> Path:
    """File to read for (cv, lang), falling back to the CV's base file.

    Mirrors data_path_for_lang: asking for a language a CV has no file for
    yields its default-language data rather than an error, so a new
    translation starts from the existing content.
    """
    path = cv_file_path(data_dir, cv, lang, default_lang, suffix)
    if path.exists():
        return path
    return cv_file_path(data_dir, cv, default_lang, default_lang, suffix)


def list_cvs(
    data_dir: Path,
    known_langs: Iterable[str],
    default_lang: str = "es",
    suffix: str = ".yaml",
) -> list[dict]:
    """Discover the CVs in data_dir as [{"name": str, "langs": [str]}].

    Groups <name>.yaml and <name>_<lang>.yaml under one entry. Files whose
    stem is not a valid CV name are ignored, so unrelated YAML in the data
    directory does not show up as a broken CV.
    """
    langs = [lang for lang in known_langs if lang != default_lang]
    found: dict[str, set[str]] = {}

    if not data_dir.is_dir():
        return []

    for path in data_dir.glob(f"*{suffix}"):
        if not path.is_file():
            continue
        stem, lang = path.stem, default_lang
        for candidate in langs:
            if stem.endswith(f"_{candidate}"):
                stem, lang = stem[: -len(candidate) - 1], candidate
                break
        try:
            validate_cv_name(stem, known_langs)
        except ValueError:
            continue
        found.setdefault(stem, set()).add(lang)

    return [
        {"name": name, "langs": sorted(found[name])} for name in sorted(found)
    ]


def _photo_data_uri(photo_path: Path, yaml_dir: Path) -> str | None:
    resolved = photo_path if photo_path.is_absolute() else yaml_dir / photo_path
    resolved = resolved.resolve()
    if not resolved.exists():
        return None
    try:
        img = Image.open(resolved)
        if img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
    except OSError:
        return None
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def render_html(
    data: dict,
    lang: str,
    show_photo: bool,
    yaml_dir: Path,
    primary_color: str | None = None,
) -> str:
    headers = get_headers(lang)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters['linkify'] = linkify
    template = env.get_template("cv.html")
    css = (TEMPLATES_DIR / "style.css").read_text(encoding="utf-8")

    if primary_color:
        css = f":root {{ --primary-color: {primary_color}; }}\n" + css

    # `meta:` may be absent entirely, or present but empty (which parses as
    # None), on a hand-written or freshly created CV.
    meta = data.get("meta") or {}
    photo_uri = None
    if show_photo and meta.get("photo"):
        photo_uri = _photo_data_uri(Path(meta["photo"]), yaml_dir)

    return template.render(data=data, headers=headers, photo_uri=photo_uri, css=css)


def generate_pdf(html_string: str, output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=html_string).write_pdf(str(output_path))
    print(f"PDF generated: {output_path}")


def build_cv(
    yaml_path: Path,
    output_path: Path,
    lang: str = "es",
    show_photo: bool = True,
) -> None:
    data = load_data(yaml_path)
    yaml_dir = yaml_path.resolve().parent
    html = render_html(data, lang, show_photo, yaml_dir)
    generate_pdf(html, output_path)


def _markdown_contact(contact: dict) -> str:
    """Contact line as markdown, mirroring the template's link handling."""
    parts = []
    for key in ("email", "location"):
        if contact.get(key):
            parts.append(str(contact[key]))
    for key, base in (
        ("linkedin", "https://linkedin.com/in/"),
        ("github", "https://github.com/"),
    ):
        value = contact.get(key)
        if not value:
            continue
        if isinstance(value, dict):
            text, url = value.get("text", ""), value.get("url", "")
        else:
            text, url = str(value), base + str(value)
        parts.append(f"[{text}]({url})" if url else text)
    return " · ".join(parts)


def render_markdown(data: dict, lang: str) -> str:
    """Render the CV as markdown, for pasting into a chat or another tool.

    Descriptions and bullets already use [text](url), so they pass through
    unchanged rather than going through linkify.
    """
    h = get_headers(lang)
    out: list[str] = []

    if data.get("name"):
        out.append(f"# {data['name']}")
    if data.get("headline"):
        out += ["", f"**{data['headline']}**"]

    contact = _markdown_contact(data.get("contact") or {})
    if contact:
        out += ["", contact]

    if data.get("summary"):
        out += ["", f"## {h['summary']}", "", str(data["summary"])]

    if data.get("experience"):
        out += ["", f"## {h['experience']}"]
        for job in data["experience"]:
            title, company = job.get("title", ""), job.get("company", "")
            out += ["", f"### {title} — {company}" if company else f"### {title}"]
            meta = [str(x) for x in (job.get("location"), job.get("dates")) if x]
            if meta:
                out += ["", f"*{' · '.join(meta)}*"]
            if job.get("description"):
                out += ["", str(job["description"])]
            if job.get("bullets"):
                out.append("")
                out += [f"- {b}" for b in job["bullets"]]

    if data.get("education"):
        out += ["", f"## {h['education']}"]
        for edu in data["education"]:
            out += ["", f"### {edu.get('institution', '')}"]
            line = " · ".join(
                str(x) for x in (edu.get("degree"), edu.get("dates")) if x
            )
            if line:
                out += ["", line]

    if data.get("publications"):
        out += ["", f"## {h['publications']}", ""]
        for pub in data["publications"]:
            title = pub.get("title", "")
            label = f"[{title}]({pub['url']})" if pub.get("url") else title
            line = f"- **{label}**"
            where = [str(x) for x in (pub.get("venue"), pub.get("year")) if x]
            if where:
                line += f" — {', '.join(where)}"
            if pub.get("note"):
                line += f" *({pub['note']})*"
            out.append(line)

    if data.get("skills"):
        out += ["", f"## {h['skills']}", ""]
        out += [
            f"- **{s.get('category', '')}:** {s.get('entries', '')}"
            for s in data["skills"]
        ]

    if data.get("languages"):
        out += ["", f"## {h['languages']}", ""]
        out += [
            f"- {lg.get('name', '')} — {lg.get('level', '')}"
            for lg in data["languages"]
        ]

    if data.get("certifications"):
        out += ["", f"## {h['certifications']}", ""]
        out += [f"- {c}" for c in data["certifications"]]

    return "\n".join(out).strip() + "\n"
