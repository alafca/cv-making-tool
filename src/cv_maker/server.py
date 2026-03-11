import webbrowser
from pathlib import Path

import yaml
from fastapi import FastAPI
from fastapi.responses import FileResponse, HTMLResponse, Response
from pydantic import BaseModel
from weasyprint import HTML

from cv_maker.generator import load_data, render_html

STATIC_DIR = Path(__file__).parent / "static"


class PreviewRequest(BaseModel):
    yaml: str
    lang: str = "es"
    show_photo: bool = True
    primary_color: str | None = None


class SaveRequest(BaseModel):
    yaml: str


def create_app(yaml_path: Path) -> FastAPI:
    app = FastAPI()
    yaml_path = yaml_path.resolve()

    @app.get("/", response_class=HTMLResponse)
    def editor_page():
        return FileResponse(STATIC_DIR / "editor.html", media_type="text/html")

    @app.get("/api/yaml")
    def read_yaml():
        text = yaml_path.read_text(encoding="utf-8")
        return {"yaml": text}

    @app.post("/api/preview", response_class=HTMLResponse)
    def preview(req: PreviewRequest):
        try:
            data = yaml.safe_load(req.yaml)
        except yaml.YAMLError as exc:
            return HTMLResponse(
                f"<pre style='color:red;padding:1em'>{exc}</pre>", status_code=200
            )
        if not data:
            return HTMLResponse(
                "<pre style='color:red;padding:1em'>Empty YAML</pre>", status_code=200
            )
        html = render_html(
            data,
            req.lang,
            req.show_photo,
            yaml_path.parent,
            primary_color=req.primary_color,
        )
        return html

    @app.post("/api/save")
    def save_yaml(req: SaveRequest):
        yaml_path.write_text(req.yaml, encoding="utf-8")
        return {"ok": True}

    @app.post("/api/pdf")
    def download_pdf(req: PreviewRequest):
        try:
            data = yaml.safe_load(req.yaml)
        except yaml.YAMLError as exc:
            return {"error": str(exc)}
        if not data:
            return {"error": "Empty YAML"}
        html = render_html(
            data,
            req.lang,
            req.show_photo,
            yaml_path.parent,
            primary_color=req.primary_color,
        )
        pdf_bytes = HTML(string=html).write_pdf()
        return Response(
            content=pdf_bytes,
            media_type="application/pdf",
            headers={"Content-Disposition": "attachment; filename=cv.pdf"},
        )

    @app.post("/api/export-html")
    def export_html(req: PreviewRequest):
        try:
            data = yaml.safe_load(req.yaml)
        except yaml.YAMLError as exc:
            return {"error": str(exc)}
        if not data:
            return {"error": "Empty YAML"}
        html = render_html(
            data,
            req.lang,
            req.show_photo,
            yaml_path.parent,
            primary_color=req.primary_color,
        )
        return Response(
            content=html,
            media_type="text/html",
            headers={"Content-Disposition": "attachment; filename=cv.html"},
        )

    return app


def run_server(yaml_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn

    app = create_app(yaml_path)
    url = f"http://{host}:{port}"
    print(f"Starting CV Maker editor at {url}")
    webbrowser.open(url)
    uvicorn.run(app, host=host, port=port)
