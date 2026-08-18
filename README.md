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

Keep several tailored versions side by side — one per employer, role, audience or
language. Each CV is a single file in `data/`, named after the CV:

```
data/cv_data.yaml        # CV "cv_data"
data/cv_data_en.yaml     # CV "cv_data_en"
data/acme.yaml           # CV "acme"
```

Each CV declares its own language through `meta.lang` in the file, which is what
picks the section headers (`Work Experience` vs `Experiencia Laboral`). English is
used when a CV does not say. Edit it under **Meta → Lang** in the fields view.
Translations are therefore just separate CVs, free to diverge in content.

In the editor, the **CV** dropdown switches between them. `+ New` starts an empty
CV, `Duplicate` copies the current one under a new name — the quickest way to spin
up a tailored version — and `Delete` removes one, keeping a `.bak` next to it.
Downloads are named after the CV (`acme.pdf`), so versions don't overwrite each
other.

CV names may contain letters, digits, hyphens and underscores.

The profile photo is off by default, which is the convention for US resumes.
Tick **Photo** in the editor, or pass `--photo` on the command line, to include it.

**Copy Markdown** puts the whole CV on the clipboard as markdown — handy for
pasting into a chat with an AI assistant for feedback.

From the command line:

```bash
uv run python -m cv_maker --list-cvs         # show available CVs
uv run python -m cv_maker --cv acme          # -> output/acme.pdf
uv run python -m cv_maker --cv acme --serve  # open the editor on that CV
```

## Tests

```bash
uv run pytest
```
