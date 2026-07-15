import html
import webbrowser
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from weasyprint import HTML

from cv_maker.generator import data_path_for_lang, render_html

STATIC_DIR = Path(__file__).parent / "static"


class PreviewRequest(BaseModel):
    yaml: str
    lang: str = "es"
    show_photo: bool = True
    primary_color: str | None = None


class SaveRequest(BaseModel):
    yaml: str
    lang: str = "es"


def _error_page(message: str) -> HTMLResponse:
    return HTMLResponse(
        f"<pre style='color:red;padding:1em;white-space:pre-wrap'>{html.escape(message)}</pre>",
        status_code=200,
    )


def create_app(yaml_path: Path, default_lang: str = "es") -> FastAPI:
    app = FastAPI()
    yaml_path = yaml_path.resolve()
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
                yaml_path.parent,
                primary_color=req.primary_color,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Render error: {exc}")

    @app.get("/", response_class=HTMLResponse)
    def editor_page():
        return FileResponse(STATIC_DIR / "editor.html", media_type="text/html")

    @app.get("/api/config")
    def config():
        return {"default_lang": default_lang}

    @app.get("/api/yaml")
    def read_yaml(lang: str = "es"):
        path = data_path_for_lang(yaml_path, lang, default_lang)
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Data file not found: {path}")
        return {"yaml": path.read_text(encoding="utf-8")}

    @app.post("/api/preview", response_class=HTMLResponse)
    def preview(req: PreviewRequest):
        try:
            return HTMLResponse(render_or_raise(req))
        except HTTPException as exc:
            # Show the problem inside the preview pane instead of failing the request.
            return _error_page(str(exc.detail))

    @app.post("/api/save")
    def save_yaml(req: SaveRequest):
        parse_yaml_or_raise(req.yaml)
        if req.lang == default_lang:
            path = yaml_path
        else:
            # Always write to the language-specific file so a first save in a
            # new language never overwrites the base data file.
            path = yaml_path.with_name(
                f"{yaml_path.stem}_{req.lang}{yaml_path.suffix}"
            )
        path.write_text(req.yaml, encoding="utf-8")
        return {"ok": True, "path": str(path)}

    @app.post("/api/pdf")
    def download_pdf(req: PreviewRequest):
        html_str = render_or_raise(req)
        pdf_bytes = HTML(string=html_str).write_pdf()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=cv.pdf"},
        )

    @app.post("/api/export-html")
    def export_html(req: PreviewRequest):
        html_str = render_or_raise(req)
        return Response(
            content=html_str,
            media_type="text/html",
            headers={"Content-Disposition": "attachment; filename=cv.html"},
        )

    return app


def run_server(
    yaml_path: Path,
    host: str = "127.0.0.1",
    port: int = 8765,
    default_lang: str = "es",
) -> None:
    import uvicorn

    app = create_app(yaml_path, default_lang=default_lang)
    url = f"http://{host}:{port}"
    print(f"Starting CV Maker editor at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host=host, port=port)
