import base64
import io
from pathlib import Path

import yaml
from jinja2 import Environment, FileSystemLoader
from PIL import Image
from weasyprint import HTML

from cv_maker.i18n import get_headers

PHOTO_SIZE = 160  # px (renders at 80px on 2x density)

TEMPLATES_DIR = Path(__file__).parent / "templates"


def load_data(yaml_path: Path) -> dict:
    with open(yaml_path, encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not data:
        raise ValueError(f"Empty or invalid YAML: {yaml_path}")
    return data


def _photo_data_uri(photo_path: Path, yaml_dir: Path) -> str | None:
    resolved = photo_path if photo_path.is_absolute() else yaml_dir / photo_path
    resolved = resolved.resolve()
    if not resolved.exists():
        return None
    img = Image.open(resolved)
    img.thumbnail((PHOTO_SIZE, PHOTO_SIZE))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    b64 = base64.b64encode(buf.getvalue()).decode()
    return f"data:image/jpeg;base64,{b64}"


def render_html(data: dict, lang: str, show_photo: bool, yaml_dir: Path) -> str:
    headers = get_headers(lang)
    env = Environment(loader=FileSystemLoader(str(TEMPLATES_DIR)), autoescape=True)
    template = env.get_template("cv.html")
    css = (TEMPLATES_DIR / "style.css").read_text(encoding="utf-8")

    photo_uri = None
    if show_photo and data.get("meta", {}).get("photo"):
        photo_uri = _photo_data_uri(Path(data["meta"]["photo"]), yaml_dir)

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
