import re
from pathlib import Path

import pytest
from PIL import Image

from scripts.build_style_contact_sheets import (
    DEFAULT_GALLERY_DIR,
    HORIZONTAL_MARGIN,
    IMAGE_BOX_HEIGHT,
    LABEL_BACKGROUND_COLOR,
    LETTERBOX_COLOR,
    SHEET_SIZE,
    STYLE_IDS,
    VERTICAL_MARGIN,
    build_contact_sheets,
    main,
)
from whiteboard_skill.styles import available_styles


def _write_gallery_images(input_dir: Path, *, missing_index: int | None = None) -> None:
    input_dir.mkdir(parents=True)
    for index, style_id in enumerate(STYLE_IDS, start=1):
        if index == missing_index:
            continue
        size = (20, 40) if index == 1 else (24 + index % 5, 18 + index % 7)
        color = (220, 40, 40) if index == 1 else (
            index * 7 % 255,
            index * 11 % 255,
            index * 13 % 255,
        )
        Image.new("RGB", size, color).save(
            input_dir / f"{index:02d}-{style_id}.png"
        )


def test_cli_builds_five_labeled_contact_sheets_without_cropping(
    tmp_path: Path,
) -> None:
    input_dir = tmp_path / "gallery"
    output_dir = tmp_path / "sheets"
    _write_gallery_images(input_dir)

    assert (
        main(
            [
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
            ]
        )
        == 0
    )

    outputs = [output_dir / f"contact-sheet-{index:02d}.png" for index in range(1, 6)]
    assert all(path.is_file() for path in outputs)
    for output in outputs:
        with Image.open(output) as sheet:
            assert sheet.size == SHEET_SIZE

    with Image.open(outputs[0]) as first_sheet:
        # The first fixture is portrait. Contain must leave horizontal letterbox
        # space; a cover/crop implementation would fill this point with red.
        assert first_sheet.getpixel(
            (HORIZONTAL_MARGIN + 10, VERTICAL_MARGIN + IMAGE_BOX_HEIGHT // 2)
        ) == LETTERBOX_COLOR
        assert first_sheet.getpixel(
            (HORIZONTAL_MARGIN + 300, VERTICAL_MARGIN + IMAGE_BOX_HEIGHT // 2)
        ) == (220, 40, 40)
        assert first_sheet.getpixel(
            (HORIZONTAL_MARGIN + 10, VERTICAL_MARGIN + IMAGE_BOX_HEIGHT + 10)
        ) == LABEL_BACKGROUND_COLOR


def test_missing_style_image_fails_before_creating_outputs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    input_dir = tmp_path / "gallery"
    output_dir = tmp_path / "sheets"
    missing_index = 17
    _write_gallery_images(input_dir, missing_index=missing_index)
    missing_name = f"{missing_index:02d}-{STYLE_IDS[missing_index - 1]}.png"

    with pytest.raises(SystemExit) as caught:
        main(
            [
                "--input-dir",
                str(input_dir),
                "--output-dir",
                str(output_dir),
            ]
        )

    assert caught.value.code == 1
    assert re.search(re.escape(missing_name), capsys.readouterr().err)
    assert not output_dir.exists()


def test_build_function_returns_outputs_in_registry_order(tmp_path: Path) -> None:
    input_dir = tmp_path / "gallery"
    output_dir = tmp_path / "sheets"
    _write_gallery_images(input_dir)

    outputs = build_contact_sheets(input_dir, output_dir)

    assert STYLE_IDS == tuple(style.id for style in available_styles())
    assert [path.name for path in outputs] == [
        f"contact-sheet-{index:02d}.png" for index in range(1, 6)
    ]


def test_committed_reference_gallery_is_complete_and_landscape() -> None:
    source_images = [
        DEFAULT_GALLERY_DIR / f"{index:02d}-{style_id}.png"
        for index, style_id in enumerate(STYLE_IDS, start=1)
    ]
    contact_sheets = [
        DEFAULT_GALLERY_DIR / f"contact-sheet-{index:02d}.png"
        for index in range(1, 6)
    ]

    assert all(path.is_file() for path in source_images + contact_sheets)
    for path in source_images:
        with Image.open(path) as image:
            width, height = image.size
            assert abs(width / height - 16 / 9) < 0.01
            image.verify()

    for path in contact_sheets:
        with Image.open(path) as image:
            assert image.size == SHEET_SIZE
            image.verify()
