import argparse
import sys
from pathlib import Path

from cv_maker.generator import build_cv, cv_file_path, list_cvs, validate_cv_name


def main():
    parser = argparse.ArgumentParser(description="Generate a PDF CV from YAML data")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/cv_data.yaml"),
        help="Path to YAML data file (default: data/cv_data.yaml)",
    )
    parser.add_argument(
        "--cv",
        help="Name of the CV to use, e.g. --cv acme picks data/acme.yaml. "
        "Defaults to the --data file.",
    )
    parser.add_argument(
        "--list-cvs",
        action="store_true",
        help="List the CVs found next to the --data file and exit",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Output PDF path (default: output/cv.pdf, or output/<cv>.pdf with --cv)",
    )
    parser.add_argument(
        "--photo",
        action="store_true",
        help="Include the profile photo (omitted by default)",
    )
    parser.add_argument(
        # Now the default. Still accepted so existing commands keep working.
        "--no-photo",
        action="store_true",
        help=argparse.SUPPRESS,
    )
    parser.add_argument(
        "--serve",
        action="store_true",
        help="Launch web editor with live preview instead of generating PDF",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8765,
        help="Port for the web editor (default: 8765)",
    )
    args = parser.parse_args()

    data_dir = args.data.parent
    suffix = args.data.suffix or ".yaml"

    if args.list_cvs:
        cvs = list_cvs(data_dir, suffix)
        if not cvs:
            print(f"No CVs found in {data_dir}")
            return
        for name in cvs:
            print(name)
        return

    data_path = args.data
    if args.cv:
        try:
            cv = validate_cv_name(args.cv)
        except ValueError as exc:
            parser.error(str(exc))
        data_path = cv_file_path(data_dir, cv, suffix)
        if not data_path.exists():
            parser.error(f"No CV named '{cv}' in {data_dir} (expected {data_path})")

    if args.serve:
        from cv_maker.server import run_server

        run_server(data_path, port=args.port)
    else:
        # Keep the historical output/cv.pdf default; only a named CV gets its
        # own filename, so tailored versions don't overwrite each other.
        default_output = Path("output") / (f"{args.cv}.pdf" if args.cv else "cv.pdf")
        output = args.output or default_output
        try:
            build_cv(data_path, output, show_photo=args.photo and not args.no_photo)
        except (OSError, ValueError) as exc:
            sys.exit(f"Error: {exc}")
