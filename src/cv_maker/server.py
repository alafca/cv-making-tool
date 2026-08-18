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
    render_markdown,
    validate_cv_name,
)
from cv_maker.i18n import supported_langs

STATIC_DIR = Path(__file__).parent / "static"


class PreviewRequest(BaseModel):
    yaml: str
    show_photo: bool = True
    primary_color: str | None = None
    # Only used to name downloaded files; the content always comes from `yaml`.
    cv: str | None = None


class SaveRequest(BaseModel):
    yaml: str
    cv: str | None = None


class CreateCvRequest(BaseModel):
    name: str
    # When set, the new CV starts as a copy of this one.
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


def _starter_yaml() -> str:
    """Minimal valid CV so a brand-new file opens cleanly in the fields view."""
    return (
        "name: Your Name\n"
        "headline: Your headline\n"
        "contact:\n"
        "  email: you@example.com\n"
        "  location: City, Country\n"
        "meta:\n"
        "  lang: en\n"
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


def _default_cv_from_path(yaml_path: Path) -> str:
    """CV name implied by the path the server was started with."""
    try:
        return validate_cv_name(yaml_path.stem)
    except ValueError:
        return DEFAULT_CV


def create_app(yaml_path: Path) -> FastAPI:
    """Editor app for the CVs in yaml_path's directory.

    Each CV is one file and declares its own language through meta.lang, so the
    server has no notion of a default language.
    """
    app = FastAPI()
    yaml_path = yaml_path.resolve()
    data_dir = yaml_path.parent
    suffix = yaml_path.suffix or ".yaml"
    default_cv = _default_cv_from_path(yaml_path)
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def valid_cv(name: str | None) -> str:
        """Validate a client-supplied CV name, defaulting to the startup one."""
        if name is None:
            return default_cv
        try:
            return validate_cv_name(name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    def known_cvs() -> list[str]:
        return list_cvs(data_dir, suffix)

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
            cv = validate_cv_name(cv)
        except ValueError:
            cv = default_cv
        return f"{cv}.{extension}"

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
        # langs only populates the per-CV meta.lang selector in the fields view.
        return {"default_cv": default_cv, "langs": list(supported_langs())}

    @app.get("/api/cvs")
    def get_cvs():
        return {"cvs": known_cvs(), "default_cv": default_cv}

    @app.post("/api/cvs")
    def create_cv(req: CreateCvRequest):
        name = valid_cv(req.name)
        if name in known_cvs():
            raise HTTPException(
                status_code=409, detail=f"A CV named '{name}' already exists."
            )

        dst = cv_file_path(data_dir, name, suffix)
        if req.copy_from:
            source = valid_cv(req.copy_from)
            src = cv_file_path(data_dir, source, suffix)
            if not src.exists():
                raise HTTPException(
                    status_code=404, detail=f"No data file found for '{source}'."
                )
            dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        else:
            dst.write_text(_starter_yaml(), encoding="utf-8")

        return {"ok": True, "name": name}

    @app.delete("/api/cvs/{name}")
    def delete_cv(name: str):
        name = valid_cv(name)
        if name not in known_cvs():
            raise HTTPException(status_code=404, detail=f"No CV named '{name}'.")
        if name == default_cv:
            raise HTTPException(
                status_code=400,
                detail=f"'{name}' is the CV this editor was started with and "
                "cannot be deleted here.",
            )

        path = cv_file_path(data_dir, name, suffix)
        # Backed up rather than merely unlinked: a mis-click should not destroy
        # a tailored CV.
        _backup(path)
        path.unlink()
        return {"ok": True, "removed": [path.name]}

    @app.get("/api/yaml")
    def read_yaml(cv: str | None = None):
        cv = valid_cv(cv)
        path = cv_file_path(data_dir, cv, suffix)
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
        path = cv_file_path(data_dir, cv, suffix)
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

    @app.post("/api/markdown")
    def markdown(req: PreviewRequest):
        data = parse_yaml_or_raise(req.yaml)
        try:
            text = render_markdown(data)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Markdown error: {exc}")
        return Response(content=text, media_type="text/markdown; charset=utf-8")

    return app


def run_server(
    yaml_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
) -> None:
    import uvicorn

    app = create_app(yaml_path)
    url = f"http://{host}:{port}"
    print(f"Starting CV Maker editor at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host=host, port=port)
