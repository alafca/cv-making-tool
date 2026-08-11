import html
import webbrowser
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from weasyprint import HTML

from cv_maker.generator import (
    DEFAULT_CV,
    cv_file_path,
    list_cvs,
    render_html,
    resolve_cv_path,
    validate_cv_name,
)
from cv_maker.i18n import supported_langs

STATIC_DIR = Path(__file__).parent / "static"


class PreviewRequest(BaseModel):
    yaml: str
    lang: str = "es"
    show_photo: bool = True
    primary_color: str | None = None
    # Only used to name downloaded files; the content always comes from `yaml`.
    cv: str | None = None


class SaveRequest(BaseModel):
    yaml: str
    lang: str = "es"
    cv: str | None = None


class CreateCvRequest(BaseModel):
    name: str
    # When set, the new CV starts as a copy of this one (all its languages).
    copy_from: str | None = None


def _error_page(message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<pre style='color:red;padding:1em;white-space:pre-wrap'>{html.escape(message)}</pre>",
        status_code=200,
    )


HISTORY_DIR = ".history"
HISTORY_KEEP = 20


def _backup(path: Path) -> None:
    """Preserve the current contents before a write replaces them.

    Writes both the adjacent .bak and a timestamped copy under .history/. The
    .bak alone is not enough: two saves in a row push the good version out of
    it, which is exactly how a section can become unrecoverable.
    """
    current = path.read_text(encoding="utf-8")
    path.with_name(path.name + ".bak").write_text(current, encoding="utf-8")

    history = path.parent / HISTORY_DIR
    history.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    snapshot = history / f"{path.stem}-{stamp}{path.suffix}"
    if not snapshot.exists():
        snapshot.write_text(current, encoding="utf-8")

    old = sorted(history.glob(f"{path.stem}-*{path.suffix}"))[:-HISTORY_KEEP]
    for stale in old:
        stale.unlink()


def _starter_yaml(lang: str) -> str:
    """Minimal valid CV so a brand-new file opens cleanly in the fields view."""
    return (
        "name: Your Name\n"
        "headline: Your headline\n"
        "contact:\n"
        "  email: you@example.com\n"
        "  location: City, Country\n"
        "meta:\n"
        f"  lang: {lang}\n"
        "summary: A short professional summary.\n"
        "experience:\n"
        "  - title: Job title\n"
        "    company: Company\n"
        "    location: City, Country\n"
        "    dates: 2024 - Present\n"
        "    description: What the role was about.\n"
        "    bullets:\n"
        "      - What you achieved, with a number in it.\n"
        "education:\n"
        "  - institution: University\n"
        "    degree: Degree\n"
        "    dates: 2018 - 2022\n"
        "skills:\n"
        "  - category: Category\n"
        "    entries: skill one, skill two\n"
    )


def _default_cv_from_path(yaml_path: Path, known_langs, default_lang: str) -> str:
    """CV name implied by the path the server was started with.

    Tolerates being pointed straight at a language variant (cv_data_en.yaml)
    and falls back to DEFAULT_CV for a stem that is not a usable CV name.
    """
    stem = yaml_path.stem
    for lang in known_langs:
        if lang != default_lang and stem.endswith(f"_{lang}"):
            stem = stem[: -len(lang) - 1]
            break
    try:
        return validate_cv_name(stem, known_langs)
    except ValueError:
        return DEFAULT_CV


def create_app(
    yaml_path: Path,
    default_lang: str = "es",
    initial_lang: str | None = None,
) -> FastAPI:
    """Editor app for the CVs in yaml_path's directory.

    default_lang is a property of the files on disk: the language held by the
    unsuffixed file. initial_lang only chooses which language the editor opens
    in, and defaults to default_lang.
    """
    app = FastAPI()
    yaml_path = yaml_path.resolve()
    data_dir = yaml_path.parent
    suffix = yaml_path.suffix or ".yaml"
    known_langs = supported_langs()
    default_cv = _default_cv_from_path(yaml_path, known_langs, default_lang)
    if initial_lang not in known_langs:
        initial_lang = default_lang
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def valid_cv(name: str | None) -> str:
        """Validate a client-supplied CV name, defaulting to the startup one."""
        if name is None:
            return default_cv
        try:
            return validate_cv_name(name, known_langs)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def known_cvs() -> list[dict]:
        return list_cvs(data_dir, known_langs, default_lang, suffix)

    def parse_yaml_or_raise(text: str) -> dict:
        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise HTTPException(status_code=400, detail=f"Invalid YAML: {exc}")
        if not data:
            raise HTTPException(status_code=400, detail="Empty YAML")
        return data

    def render_or_raise(req: PreviewRequest) -> str:
        data = parse_yaml_or_raise(req.yaml)
        try:
            return render_html(
                data,
                req.lang,
                req.show_photo,
                data_dir,
                primary_color=req.primary_color,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Render error: {exc}")

    def download_name(req: PreviewRequest, extension: str) -> str:
        """Name downloads after the CV so tailored versions don't collide."""
        cv = req.cv if req.cv else default_cv
        try:
            cv = validate_cv_name(cv, known_langs)
        except ValueError:
            cv = default_cv
        lang = req.lang if req.lang in known_langs else default_lang
        return f"{cv}_{lang}.{extension}"

    @app.get("/", response_class=HTMLResponse)
    def editor_page():
        # Never cache the editor: a page held over from an older version writes
        # YAML using its own idea of which sections exist, which silently drops
        # any section it does not know about.
        return FileResponse(
            STATIC_DIR / "editor.html",
            media_type="text/html",
            headers={"Cache-Control": "no-store, must-revalidate"},
        )

    @app.get("/api/config")
    def config():
        return {
            "default_lang": default_lang,
            "initial_lang": initial_lang,
            "default_cv": default_cv,
            "langs": list(known_langs),
        }

    @app.get("/api/cvs")
    def get_cvs():
        return {"cvs": known_cvs(), "default_cv": default_cv}

    @app.post("/api/cvs")
    def create_cv(req: CreateCvRequest):
        name = valid_cv(req.name)
        if any(entry["name"] == name for entry in known_cvs()):
            raise HTTPException(
                status_code=409, detail=f"A CV named '{name}' already exists."
            )

        created = []
        if req.copy_from:
            source = valid_cv(req.copy_from)
            for lang in known_langs:
                src = cv_file_path(data_dir, source, lang, default_lang, suffix)
                if not src.exists():
                    continue
                dst = cv_file_path(data_dir, name, lang, default_lang, suffix)
                dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
                created.append(lang)
            if not created:
                raise HTTPException(
                    status_code=404, detail=f"No data files found for '{source}'."
                )
        else:
            dst = cv_file_path(data_dir, name, default_lang, default_lang, suffix)
            dst.write_text(_starter_yaml(default_lang), encoding="utf-8")
            created.append(default_lang)

        return {"ok": True, "name": name, "langs": sorted(created)}

    @app.delete("/api/cvs/{name}")
    def delete_cv(name: str):
        name = valid_cv(name)
        existing = {entry["name"] for entry in known_cvs()}
        if name not in existing:
            raise HTTPException(status_code=404, detail=f"No CV named '{name}'.")
        if name == default_cv:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is the CV this editor was started with and "
                "is the fallback for the others; it cannot be deleted here.",
            )

        removed = []
        for lang in known_langs:
            path = cv_file_path(data_dir, name, lang, default_lang, suffix)
            if path.exists():
                # Backed up rather than merely unlinked: a mis-click should not
                # destroy a tailored CV.
                _backup(path)
                path.unlink()
                removed.append(path.name)
        return {"ok": True, "removed": removed}

    @app.get("/api/yaml")
    def read_yaml(lang: str = "es", cv: str | None = None):
        cv = valid_cv(cv)
        path = resolve_cv_path(data_dir, cv, lang, default_lang, suffix)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Data file not found: {path}")
        return {"yaml": path.read_text(encoding="utf-8"), "cv": cv, "path": str(path)}

    @app.post("/api/preview", response_class=HTMLResponse)
    def preview(req: PreviewRequest):
        try:
            html_str = render_or_raise(req)
        except HTTPException as exc:
            # Show the problem inside the preview pane instead of failing the request.
            return _error_page(str(exc.detail))
        try:
            page_count = len(HTML(string=html_str).render().pages)
        except Exception:
            page_count = 0
        return HTMLResponse(html_str, headers={"X-Page-Count": str(page_count)})

    @app.post("/api/save")
    def save_yaml(req: SaveRequest):
        parse_yaml_or_raise(req.yaml)
        cv = valid_cv(req.cv)
        # Always write the language-specific file for non-default languages so a
        # first save in a new language never overwrites the base data file.
        path = cv_file_path(data_dir, cv, req.lang, default_lang, suffix)
        if path.exists():
            # Keep the previous version so one bad save is always reversible.
            _backup(path)
        path.write_text(req.yaml, encoding="utf-8")
        return {"ok": True, "path": str(path), "cv": cv}

    @app.post("/api/pdf")
    def download_pdf(req: PreviewRequest):
        html_str = render_or_raise(req)
        pdf_bytes = HTML(string=html_str).write_pdf()
        filename = download_name(req, "pdf")
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @app.post("/api/export-html")
    def export_html(req: PreviewRequest):
        html_str = render_or_raise(req)
        filename = download_name(req, "html")
        return Response(
            content=html_str,
            media_type="text/html",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    return app


def run_server(
    yaml_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    default_lang: str = "es",
    initial_lang: str | None = None,
) -> None:
    import uvicorn

    app = create_app(yaml_path, default_lang=default_lang, initial_lang=initial_lang)
    url = f"http://{host}:{port}"
    print(f"Starting CV Maker editor at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host=host, port=port)
