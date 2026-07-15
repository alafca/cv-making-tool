import argparse
from pathlib import Path

from cv_maker.generator import build_cv, data_path_for_lang


def main():
    parser = argparse.ArgumentParser(description="Generate a PDF CV from YAML data")
    parser.add_argument(
        "--data",
        type=Path,
        default=Path("data/cv_data.yaml"),
        help="Path to YAML data file (default: data/cv_data.yaml)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("output/cv.pdf"),
        help="Output PDF path (default: output/cv.pdf)",
    )
    parser.add_argument(
        "--lang",
        choices=["en", "es"],
        default="es",
        help="Language for section headers (default: es)",
    )
    parser.add_argument(
        "--no-photo",
        action="store_true",
        help="Disable profile photo",
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

    if args.serve:
        from cv_maker.server import run_server

        run_server(args.data, port=args.port, default_lang=args.lang)
    else:
        data_path = data_path_for_lang(args.data, args.lang)
        build_cv(data_path, args.output, args.lang, show_photo=not args.no_photo)
