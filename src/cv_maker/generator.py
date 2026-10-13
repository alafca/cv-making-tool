import base64
import io
import re
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from markupsafe import Markup, escape
from PIL import Image
from weasyprint import HTML

from cv_maker.i18n import get_headers

PHOTO_SIZE = 160  # px (renders at 80px on 2x density)

TEMPLATES_DIR = Path(__file__).parent / "templates"
STYLES_DIR = TEMPLATES_DIR / "styles"

# Visual styles a CV can pick through meta.style. Each id names a stylesheet in
# templates/styles/ that is appended after the base one; "classic" is the base
# stylesheet alone. Ordered as the editor shows them.
STYLES: dict[str, dict[str, str]] = {
    "classic": {
        "label": "Classic",
        "detail": "Calibri 10pt, the original layout",
    },
    "calibri-airy": {
        "label": "Calibri airy",
        "detail": "Calibri 10.5pt, wider margins, more space between bullets",
    },
    "cambria": {
        "label": "Cambria",
        "detail": "Cambria serif body with Calibri headings, lining numerals",
    },
    "segoe": {
        "label": "Segoe UI",
        "detail": "Segoe UI, a wider humanist sans, 10pt",
    },
}
DEFAULT_STYLE = "classic"

_LINK_RE = re.compile(r'\[([^\]]+)\]\(([^)]+)\)')
# **text** for sparing emphasis; the markdown form, so exports stay markdown.
# No whitespace just inside the asterisks, as in markdown, so "2 ** 8" is left
# alone.
_BOLD_RE = re.compile(r'\*\*(?=\S)([^*\n]+?)(?<=\S)\*\*')

# A "CV" is one tailored version of the resume, held in a single file named
# after it: <name>.yaml. A CV declares its own language via meta.lang, so
# translations are simply separate CVs rather than variants of one.
DEFAULT_CV = "cv_data"

# Conservative on purpose: the name reaches the filesystem, so anything that
# could escape the data directory (dots, separators) must not get through.
_CV_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


# Only these become clickable. Anything else, javascript: above all, keeps its
# label as plain text: the preview and the exported HTML run in a browser.
_SAFE_URL_RE = re.compile(r"^(?:https?:|mailto:)", re.IGNORECASE)

# The accent colour reaches a <style> block, so it must be a colour and nothing else.
_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")


def _link(match: re.Match) -> str:
    label, url = match.group(1), match.group(2).strip()
    if _SAFE_URL_RE.match(url):
        return f'<a href="{url}">{label}</a>'
    return label


def linkify(text):
    """Convert [text](url) to <a> and **text** to <strong>, escaping the rest.

    Only http, https and mailto links become anchors; any other scheme keeps
    its label as plain text.
    """
    escaped = str(escape(text))
    result = _LINK_RE.sub(_link, escaped)
    result = _BOLD_RE.sub(r"<strong>\1</strong>", result)
    return Markup(result)


def plain_text(text) -> str:
    """The words a reader or a parser sees: markup removed, links kept as text."""
    text = _LINK_RE.sub(r"\1", str(text))
    return _BOLD_RE.sub(r"\1", text)


def display_url(url: str) -> str:
    """A URL as a human would write it on a resume: no scheme, no trailing slash.

    ATS parsers read the text layer, not the href, so a contact line showing a
    bare handle ("jane-doe") gives them nothing to file under a profile field.
    Showing "linkedin.com/in/jane-doe" is both readable and machine-usable.
    """
    text = str(url or "").strip()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
            break
    text = text.removeprefix("www.")
    return text.rstrip("/")


def contact_handle(value, base: str) -> dict:
    """Normalise a contact entry to {'text', 'url'} with a URL-shaped label."""
    if isinstance(value, dict):
        url = str(value.get("url") or "").strip()
        if not url and value.get("text"):
            url = base + str(value["text"]).strip()
        label = display_url(url) if url else str(value.get("text") or "")
    else:
        handle = str(value or "").strip()
        url = base + handle if handle else ""
        label = display_url(url) if url else handle
    return {"text": label, "url": url}


def load_data(yaml_path: Path) -> dict:
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data:
        raise ValueError(f"Empty or invalid YAML: {yaml_path}")
    return data


def validate_cv_name(name: str) -> str:
    """Return name unchanged if it is usable as a CV identifier, else raise."""
    if not isinstance(name, str) or not _CV_NAME_RE.match(name):
        raise ValueError(
            f"Invalid CV name: {name!r}. Use letters, digits, hyphens and "
            "underscores (max 64 characters, starting with a letter or digit)."
        )
    return name


def cv_file_path(data_dir: Path, cv: str, suffix: str = ".yaml") -> Path:
    """The single file holding this CV."""
    return data_dir / f"{cv}{suffix}"


def list_cvs(data_dir: Path, suffix: str = ".yaml") -> list[str]:
    """Names of the CVs in data_dir, sorted.

    Files whose stem is not a usable CV name are skipped, so unrelated YAML in
    the data directory does not show up as a broken CV.
    """
    if not data_dir.is_dir():
        return []
    names = []
    for path in sorted(data_dir.glob(f"*{suffix}")):
        if not path.is_file():
            continue
        try:
            names.append(validate_cv_name(path.stem))
        except ValueError:
            continue
    return names


def cv_lang(data: dict) -> str:
    """Language a CV renders its section headers in. English unless it says."""
    return (data.get("meta") or {}).get("lang") or "en"


def cv_style(data: dict) -> str:
    """Visual style a CV renders in. Classic unless it names a known one.

    An unknown value falls back rather than failing: a CV written against a
    newer version of the tool should still render.
    """
    style = (data.get("meta") or {}).get("style")
    return style if style in STYLES else DEFAULT_STYLE


def style_css(style: str) -> str:
    """The stylesheet a style appends to the base one; empty for classic."""
    if style == DEFAULT_STYLE:
        return ""
    return (STYLES_DIR / f"{style}.css").read_text(encoding="utf-8")


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
    show_photo: bool,
    yaml_dir: Path,
    primary_color: str | None = None,
) -> str:
    headers = get_headers(cv_lang(data))
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    env.filters['linkify'] = linkify
    env.filters['display_url'] = display_url
    env.globals['contact_handle'] = contact_handle
    template = env.get_template("cv.html")
    css = (TEMPLATES_DIR / "style.css").read_text(encoding="utf-8")
    extra = style_css(cv_style(data))
    if extra:
        css = css + "\n" + extra

    # Anything that is not a hex colour is ignored rather than pasted into CSS.
    if primary_color and _HEX_COLOR_RE.match(primary_color):
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
    show_photo: bool = False,
) -> None:
    data = load_data(yaml_path)
    yaml_dir = yaml_path.resolve().parent
    html = render_html(data, show_photo, yaml_dir)
    generate_pdf(html, output_path)


def _markdown_contact(contact: dict) -> str:
    """Contact line as markdown, mirroring the template's link handling."""
    parts = []
    for key in ("email", "phone", "location"):
        if contact.get(key):
            parts.append(str(contact[key]))
    for key, base in (
        ("linkedin", "https://linkedin.com/in/"),
        ("github", "https://github.com/"),
    ):
        value = contact.get(key)
        if not value:
            continue
        handle = contact_handle(value, base)
        parts.append(
            f"[{handle['text']}]({handle['url']})" if handle["url"] else handle["text"]
        )
    return " · ".join(parts)


def render_markdown(data: dict) -> str:
    """Render the CV as markdown, for pasting into a chat or another tool.

    Descriptions and bullets already use [text](url), so they pass through
    unchanged rather than going through linkify.
    """
    h = get_headers(cv_lang(data))
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
