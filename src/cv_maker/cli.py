import argparse
from pathlib import Path

from cv_maker.generator import build_cv


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
    args = parser.parse_args()
    build_cv(args.data, args.output, args.lang, show_photo=not args.no_photo)
