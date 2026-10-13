# cv-making-tool

Write CVs in YAML, edit them in a web editor with a live preview, export PDF,
HTML or Markdown, and check how each version survives ATS parsing.

## Install

You need [uv](https://docs.astral.sh/uv/). `uv run` creates the environment
and installs the Python dependencies on first use.

PDF rendering uses [WeasyPrint](https://doc.courtbouillon.org/weasyprint/stable/first_steps.html),
which needs Pango on the system:

| System | Command |
|---|---|
| Debian, Ubuntu | `sudo apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz-subset0` |
| Fedora | `sudo dnf install pango` |
| macOS | `brew install pango` |
| Windows | Install the GTK3 runtime, as in the WeasyPrint installation guide linked above |

## Usage

### Web editor (recommended)

```bash
uv run python -m cv_maker --serve
```

Opens a browser at `http://127.0.0.1:8765` where you can edit your YAML, see a live preview, change language/color/photo, and download PDF or HTML.

On a fresh clone `data/` is empty, so the first `--serve` writes a starter CV
to `data/cv_data.yaml` and opens it. It never overwrites an existing CV.

If the same CV is open in two tabs, saving from the older one asks before
overwriting the newer version on disk, and keeps a backup either way.

Use `--port` to change the default port:

```bash
uv run python -m cv_maker --serve --port 3000
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

## Links and emphasis

Descriptions and bullets take `[text](url)` for a link and `**text**` for bold.
Only `http`, `https` and `mailto` links become clickable; any other target keeps
its label as plain text.
Use bold sparingly, on the result in a bullet rather than the verb or a tool
name: a handful per page reads as signposting, more reads as keyword stuffing.
The ATS check and the markdown export see the plain words either way.

## Styles

A CV picks its typeface and spacing through `meta.style`, next to `meta.lang`:

```yaml
meta:
  lang: en
  style: cambria
```

The **Style** dropdown in the editor toolbar (and the Style field under Meta)
writes that value, so the CLI and the editor render the same thing. Leave it
out for the classic look.

| id             | What it is                                                   |
|----------------|--------------------------------------------------------------|
| `classic`      | Calibri 10pt, the original layout                            |
| `calibri-airy` | Calibri 10.5pt, wider margins, more space between bullets    |
| `cambria`      | Cambria serif body with Calibri headings, lining numerals    |
| `segoe`        | Segoe UI, a wider humanist sans, 10pt                        |

Each style is one stylesheet in `src/cv_maker/templates/styles/`, appended
after the base one, so a style only states what it changes. The fonts are the
ones Windows ships; on other systems the sheets fall back to Carlito, Caladea
and Noto Sans, which have the same metrics.

## Your data stays local

`data/` is ignored by git, on purpose: it holds names, employers, contact
details and photos. The editor keeps its own safety copies there (`*.bak` and
`.history/`), and a fresh clone gets an empty `data/` to start from.

**Copy Markdown** puts the whole CV on the clipboard as markdown — handy for
pasting into a chat with an AI assistant for feedback.

From the command line:

```bash
uv run python -m cv_maker --list-cvs         # show available CVs
uv run python -m cv_maker --cv acme          # -> output/acme.pdf
uv run python -m cv_maker --cv acme --serve  # open the editor on that CV
```

## ATS checks

Applicant tracking systems do not see the page; they see whatever text they can
pull out of the PDF. **ATS check** in the editor, or `--ats-check` on the command
line, renders the real PDF, extracts it with `pdfminer.six` the way a parser
would, and reports what did not survive:

```bash
uv run python -m cv_maker --cv acme --ats-check
uv run python -m cv_maker --cv acme --ats-check --job-description jd.txt
```

It shows three scores side by side and never blends them:

- **Built-in**, this app's own checks, described below.
- **Lever-style parse**, from [pyresume](https://github.com/wespiper/pyresume),
  a parser that approximates Lever's ATS. It has no score of its own, so the
  number is how much of your YAML it got back, weighted towards contact
  details and job titles, with a list of what it lost.
- **ats-resume-scorer**, from the
  [PyPI package](https://pypi.org/project/ats-resume-scorer/) of that name: a
  rules-based scorer with seven weighted categories and a letter grade. Three
  of them score against a job description, so it only runs when you give one.
  Its own parser is crude, and the panel says what it found; read the number
  as what a weak parser makes of the page. It pins a numpy with no wheel for
  Python 3.13, so it runs in its own Python 3.12 environment through `uv`.
  The first check installs about 400 MB; later checks take about ten seconds.
  For that reason it is **off by default**.

`CV_MAKER_EXTERNAL_ATS` chooses what runs: `lever` (the default) runs the
Lever-style parse only, `all` adds ats-resume-scorer, `off` skips both. The
test suite runs with `off`; `uv run pytest --external` also runs the scorer
once.

The built-in check looks at four things:

- **Parse-back** — do the section headings come back as whole words, are the
  contact details in the text layer rather than only in a link, does every
  bullet survive, is any text extracting one character at a time.
- **Structure** — phone, email, location, dates a date regex can read,
  acronyms spelled out at least once, page count, availability text sitting in
  a field that will be filed as a location.
- **Content** — bullets carrying a number, and opening with an action rather
  than an activity.
- **Keywords** — with a job description, which of its distinctive terms are
  missing from the CV.

The command exits non-zero if anything critical or high is found, so it works as
a pre-send check or in CI.

## Tests

```bash
uv run pytest                              # unit + endpoint tests (fast)
uv run pytest -m browser                   # drive the editor in a real browser
CV_MAKER_HEADED=1 uv run pytest -m browser # ...and watch it happen
uv run pytest --browser                    # everything
```

Browser tests drive the real editor through Playwright, using whichever
Chromium-based browser is installed: Brave, Chrome, Edge or Chromium, on
Windows, macOS or Linux. Point `CV_MAKER_BROWSER` at a browser binary to choose
one, or run `uv run playwright install chromium` to use Playwright's own. They
are deselected by default because they take a couple of minutes.

CI runs the fast suite on Linux on every push.

They never read or write `data/`. Each test gets a temp directory holding
`tests/fixtures/testing_cv.yaml`, a fictional CV that is deliberately
ATS-clean, and `testing_cv_flawed.yaml`, its mirror image where every check
should fire. Keep the pair in sync when adding a check.

## License

MIT. See [LICENSE](LICENSE).
