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

## Multiple CVs

You can keep several tailored versions side by side — one per employer, role or
audience. Each CV is a data file in `data/`, and each can have its own language
variants:

```
data/cv_data.yaml        # CV "cv_data", default language
data/cv_data_en.yaml     # CV "cv_data", English
data/acme.yaml           # CV "acme", default language
data/acme_en.yaml        # CV "acme", English
```

In the editor, the **CV** dropdown switches between them. `+ New` starts an empty
CV, `Duplicate` copies the current one (all its languages) under a new name —
the quickest way to spin up a tailored version — and `Delete` removes one, keeping
a `.bak` of every file it removes. Downloads are named after the CV and language
(`acme_en.pdf`), so tailored versions don't overwrite each other.

CV names may contain letters, digits, hyphens and underscores.

**Copy Markdown** puts the whole CV on the clipboard as markdown — handy for
pasting into a chat with an AI assistant for feedback.

From the command line:

```bash
uv run python -m cv_maker --list-cvs              # show available CVs
uv run python -m cv_maker --cv acme --lang en     # -> output/acme.pdf
uv run python -m cv_maker --cv acme --serve       # open the editor on that CV
```

## Tests

```bash
uv run pytest
```
