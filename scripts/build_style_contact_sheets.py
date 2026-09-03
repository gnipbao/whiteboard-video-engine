"""Build five labeled contact sheets for the Little Match Girl style gallery."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

# Keep this tuple aligned with BUILTIN_STYLES in src/whiteboard_skill/styles.py.
STYLE_IDS: tuple[str, ...] = (
    "warm-crayon-storybook",
    "colored-pencil-diary",
    "clean-whiteboard",
    "minimal-line-explainer",
    "marker-whiteboard",
    "rough-diagram",
    "pressure-ink-notes",
    "semantic-ink",
    "anime-graphite",
    "kid-crayon",
    "raw-kid-crayon",
    "bean-doodle-infographic",
    "organic-contour-doodle",
    "naive-marker-notes",
    "notebook-pencil-doodle",
    "ballpoint-scribble",
    "inked-storybook",
    "emotional-watercolor-sketch",
    "ink-wash-minimal",
    "retro-gouache-concept",
    "nordic-gouache-storybook",
    "sunlit-storybook",
    "warm-flat-storybook",
    "zine-riso-collage",
    "manga-screentone",
    "linocut-editorial",
    "blueprint-pencil",
    "editorial-portrait",
    "ms-paint-doodle",
    "real-crayon-paper",
)

COLUMNS = 3
ROWS = 2
IMAGES_PER_SHEET = COLUMNS * ROWS
CELL_WIDTH = 600
IMAGE_BOX_HEIGHT = 400
LABEL_HEIGHT = 56
CELL_HEIGHT = IMAGE_BOX_HEIGHT + LABEL_HEIGHT
HORIZONTAL_MARGIN = 36
VERTICAL_MARGIN = 72
HORIZONTAL_GAP = 24
VERTICAL_GAP = 24
SHEET_SIZE = (
    HORIZONTAL_MARGIN * 2
    + CELL_WIDTH * COLUMNS
    + HORIZONTAL_GAP * (COLUMNS - 1),
    VERTICAL_MARGIN * 2
    + CELL_HEIGHT * ROWS
    + VERTICAL_GAP * (ROWS - 1),
)
IMAGE_BOX_SIZE = (CELL_WIDTH, IMAGE_BOX_HEIGHT)

SHEET_BACKGROUND_COLOR = (232, 229, 221)
LETTERBOX_COLOR = (250, 248, 243)
LABEL_BACKGROUND_COLOR = (31, 35, 42)
LABEL_TEXT_COLOR = (248, 248, 246)
BORDER_COLOR = (188, 184, 175)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GALLERY_DIR = (
    REPOSITORY_ROOT / "docs" / "assets" / "style-gallery" / "little-match-girl"
)


def expected_style_images(input_dir: Path) -> list[tuple[int, str, Path]]:
    """Return all 30 registered gallery images, failing before any output is written."""

    input_dir = Path(input_dir)
    images = [
        (index, style_id, input_dir / f"{index:02d}-{style_id}.png")
        for index, style_id in enumerate(STYLE_IDS, start=1)
    ]
    missing = [path.name for _, _, path in images if not path.is_file()]
    if missing:
        names = ", ".join(missing)
        raise FileNotFoundError(
            f"Missing {len(missing)} required style gallery image(s) in "
            f"{input_dir}: {names}"
        )
    return images


def _label_font() -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    """Use Pillow's bundled font so the script has no system-font dependency."""

    try:
        return ImageFont.load_default(size=22)
    except TypeError:  # pragma: no cover - compatibility with older Pillow releases
        return ImageFont.load_default()


def _contain_with_letterbox(path: Path) -> Image.Image:
    """Fit an image inside the fixed box without cropping any source content."""

    with Image.open(path) as opened:
        source = ImageOps.exif_transpose(opened).convert("RGBA")
        source.load()

    contained = ImageOps.contain(
        source,
        IMAGE_BOX_SIZE,
        method=Image.Resampling.LANCZOS,
    )
    letterbox = Image.new("RGB", IMAGE_BOX_SIZE, LETTERBOX_COLOR)
    position = (
        (CELL_WIDTH - contained.width) // 2,
        (IMAGE_BOX_HEIGHT - contained.height) // 2,
    )
    letterbox.paste(contained, position, contained)
    return letterbox


def _draw_label(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    y: int,
    index: int,
    style_id: str,
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
) -> None:
    label_top = y + IMAGE_BOX_HEIGHT
    draw.rectangle(
        (x, label_top, x + CELL_WIDTH - 1, label_top + LABEL_HEIGHT - 1),
        fill=LABEL_BACKGROUND_COLOR,
    )
    label = f"{index:02d}  {style_id}"
    left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
    text_x = x + (CELL_WIDTH - (right - left)) // 2 - left
    text_y = label_top + (LABEL_HEIGHT - (bottom - top)) // 2 - top
    draw.text((text_x, text_y), label, font=font, fill=LABEL_TEXT_COLOR)


def _render_sheet(entries: Sequence[tuple[int, str, Path]]) -> Image.Image:
    if len(entries) != IMAGES_PER_SHEET:
        raise ValueError(
            f"Each contact sheet requires exactly {IMAGES_PER_SHEET} images"
        )

    sheet = Image.new("RGB", SHEET_SIZE, SHEET_BACKGROUND_COLOR)
    draw = ImageDraw.Draw(sheet)
    font = _label_font()

    for slot, (index, style_id, path) in enumerate(entries):
        row, column = divmod(slot, COLUMNS)
        x = HORIZONTAL_MARGIN + column * (CELL_WIDTH + HORIZONTAL_GAP)
        y = VERTICAL_MARGIN + row * (CELL_HEIGHT + VERTICAL_GAP)
        sheet.paste(_contain_with_letterbox(path), (x, y))
        _draw_label(
            draw,
            x=x,
            y=y,
            index=index,
            style_id=style_id,
            font=font,
        )
        draw.rectangle(
            (x, y, x + CELL_WIDTH - 1, y + CELL_HEIGHT - 1),
            outline=BORDER_COLOR,
            width=1,
        )

    return sheet


def build_contact_sheets(input_dir: Path, output_dir: Path) -> list[Path]:
    """Build five 3x2 contact sheets in registry order."""

    images = expected_style_images(input_dir)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    outputs: list[Path] = []
    for offset in range(0, len(images), IMAGES_PER_SHEET):
        sheet_number = offset // IMAGES_PER_SHEET + 1
        sheet = _render_sheet(images[offset : offset + IMAGES_PER_SHEET])
        output_path = output_dir / f"contact-sheet-{sheet_number:02d}.png"
        sheet.save(output_path, format="PNG", optimize=True)
        outputs.append(output_path)
    return outputs


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Build five labeled 3x2 contact sheets for the style gallery."
    )
    parser.add_argument(
        "--input-dir",
        type=Path,
        default=DEFAULT_GALLERY_DIR,
        help="Directory containing 01-... through 30-... PNG files.",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_GALLERY_DIR,
        help="Directory for contact-sheet-01.png through contact-sheet-05.png.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        outputs = build_contact_sheets(args.input_dir, args.output_dir)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"build_style_contact_sheets: {exc}\n")
    for output in outputs:
        print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
