# cv-making-tool

Generate a PDF CV from a YAML data file. Includes a web editor with live preview.

## Usage

### Web editor (recommended)

```bash
python -m uv run python -m cv_maker --serve
```

Opens a browser at `http://127.0.0.1:8765` where you can edit your YAML, see a live preview, change language/color/photo, and download PDF or HTML.

Use `--port` to change the default port:

```bash
python -m uv run python -m cv_maker --serve --port 3000
```
