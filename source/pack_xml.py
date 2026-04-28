"""Build the one-file Prey Thai patch.

The output is a normal zip-format CryEngine .pak. It includes both the XML
localization files and the Thai UI font assets, so installation is a single
copy-over of release/English_xml_patch.pak.
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SOURCE_DIR = ROOT / "source"
LOC_SRC = SOURCE_DIR / "loc_src"
RELEASE_DIR = ROOT / "release"
DEFAULT_FONT_ZIP = RELEASE_DIR / "patch_thai_font_actual.zip"
DEFAULT_OUTPUT = RELEASE_DIR / "English_xml_patch.pak"


def add_tree(zipf: zipfile.ZipFile, src_dir: Path) -> int:
    count = 0
    for path in sorted(src_dir.rglob("*")):
        if path.is_file():
            zipf.write(path, path.relative_to(src_dir).as_posix())
            count += 1
    return count


def add_font_zip(zipf: zipfile.ZipFile, font_zip: Path) -> int:
    count = 0
    with zipfile.ZipFile(font_zip, "r") as fonts:
        for info in sorted(fonts.infolist(), key=lambda item: item.filename):
            if info.is_dir():
                continue
            zipf.writestr(info, fonts.read(info.filename))
            count += 1
    return count


def main() -> None:
    parser = argparse.ArgumentParser(description="Pack Prey Thai XML + fonts into one .pak")
    parser.add_argument("--loc-src", type=Path, default=LOC_SRC, help="Source localization XML directory")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output .pak path")
    parser.add_argument(
        "--font-zip",
        type=Path,
        default=DEFAULT_FONT_ZIP,
        help="Thai font asset zip to merge into the .pak",
    )
    parser.add_argument(
        "--no-fonts",
        action="store_true",
        help="Build XML-only patch, matching the old two-file packaging flow",
    )
    args = parser.parse_args()

    loc_src = args.loc_src.resolve()
    output = args.output.resolve()
    font_zip = args.font_zip.resolve()

    if not loc_src.is_dir():
        raise SystemExit(f"Localization source not found: {loc_src}")
    if not args.no_fonts and not font_zip.is_file():
        raise SystemExit(f"Font zip not found: {font_zip}")

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zipf:
        xml_count = add_tree(zipf, loc_src)
        font_count = 0 if args.no_fonts else add_font_zip(zipf, font_zip)

    print(f"Created {output}")
    print(f"  XML files: {xml_count}")
    print(f"  Font files: {font_count}")


if __name__ == "__main__":
    main()
