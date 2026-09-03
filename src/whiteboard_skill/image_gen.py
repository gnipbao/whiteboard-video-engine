"""Scene image generation and quality checks."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageOps

from .fingerprints import file_sha256, provider_identity, stable_fingerprint
from .logging_setup import logger
from .models import Scene
from .preprocess import quality_check
from .providers import ImageProvider
from .providers.lineart import LineArtProvider

REGISTERED_CANVAS_NORMALIZATION_VERSION = 2
_NEAR_MATCHING_ASPECT_RATIO_TOLERANCE = 0.005


def generate_scene_images(
    scenes: list[Scene],
    provider: ImageProvider,
    images_dir: Path,
    size: tuple[int, int],
    resume: bool = False,
    *,
    asset_role: str = "lineart",
) -> list[Scene]:
    """Generate or reuse source-color or direct line-art scene images."""

    if asset_role not in {"source", "lineart"}:
        raise ValueError("asset_role must be source or lineart")
    images_dir.mkdir(parents=True, exist_ok=True)
    for scene in scenes:
        out_path = images_dir / f"scene_{scene.id:02d}.png"
        generation_fingerprint = stable_fingerprint(
            {
                "asset_role": asset_role,
                "model": getattr(provider, "model", None),
                "normalization_version": (
                    REGISTERED_CANVAS_NORMALIZATION_VERSION
                    if asset_role == "source"
                    else None
                ),
                "prompt": scene.image_prompt,
                "provider": provider_identity(provider),
                "quality": getattr(provider, "quality", None),
                "size": size,
            }
        )
        if (
            resume
            and out_path.exists()
            and scene.source_generation_fingerprint == generation_fingerprint
        ):
            _verify_image(out_path)
            if asset_role == "source":
                _normalize_registered_canvas(out_path, out_path, size)
            _assign_scene_image(scene, out_path, asset_role)
            continue
        attempts = 1 if asset_role == "source" else 3
        for attempt in range(1, attempts + 1):
            provider.generate(scene.image_prompt, out_path, size)
            _verify_image(out_path)
            if asset_role == "source":
                _normalize_registered_canvas(out_path, out_path, size)
                break
            ratio = quality_check(out_path, size)
            if 0.003 <= ratio <= 0.32:
                break
            logger.warning("scene {} image foreground ratio {:.3f} outside target range on attempt {}", scene.id, ratio, attempt)
        _assign_scene_image(scene, out_path, asset_role)
        scene.source_generation_fingerprint = generation_fingerprint
    return scenes


def use_precomputed_source_images(
    scenes: list[Scene],
    images_dir: Path,
    *,
    output_dir: Path | None = None,
    size: tuple[int, int] | None = None,
    resume: bool = False,
) -> list[Scene]:
    """Attach precomputed storyboards, optionally normalizing registered copies."""

    if (output_dir is None) != (size is None):
        raise ValueError("output_dir and size must be provided together")
    if output_dir is not None:
        output_dir.mkdir(parents=True, exist_ok=True)

    for scene in scenes:
        candidates = [
            images_dir / f"scene_{scene.id:02d}{suffix}"
            for suffix in (".png", ".webp", ".jpg", ".jpeg")
        ]
        source = next((candidate for candidate in candidates if candidate.exists()), None)
        if source is None:
            raise FileNotFoundError(
                f"Missing precomputed storyboard scene_{scene.id:02d}.png/.webp/.jpg in {images_dir}"
            )
        _verify_image(source)
        if output_dir is None or size is None:
            scene.source_image_path = source.resolve()
            scene.source_generation_fingerprint = file_sha256(source)
            continue
        normalized = output_dir / f"scene_{scene.id:02d}.png"
        generation_fingerprint = stable_fingerprint(
            {
                "normalization_version": REGISTERED_CANVAS_NORMALIZATION_VERSION,
                "precomputed_sha256": file_sha256(source),
                "size": size,
            }
        )
        if (
            not resume
            or not normalized.exists()
            or scene.source_generation_fingerprint != generation_fingerprint
        ):
            _normalize_registered_canvas(source, normalized, size)
        _verify_image(normalized)
        scene.source_image_path = normalized
        scene.source_generation_fingerprint = generation_fingerprint
    return scenes


def extract_scene_lineart(
    scenes: list[Scene],
    provider: LineArtProvider,
    lineart_dir: Path,
    size: tuple[int, int],
    resume: bool = False,
) -> list[Scene]:
    """Extract pixel-registered line art from each scene's color source."""

    lineart_dir.mkdir(parents=True, exist_ok=True)
    for scene in scenes:
        if not scene.source_image_path:
            raise RuntimeError(f"Scene {scene.id} has no source_image_path")
        out_path = lineart_dir / f"scene_{scene.id:02d}.png"
        source_fingerprint = stable_fingerprint(
            {
                "normalization_version": REGISTERED_CANVAS_NORMALIZATION_VERSION,
                "provider": provider_identity(provider),
                "provider_name": getattr(provider, "name", None),
                "size": size,
                "source_sha256": file_sha256(scene.source_image_path),
            }
        )
        if (
            not resume
            or not out_path.exists()
            or scene.lineart_source_fingerprint != source_fingerprint
        ):
            provider.extract(scene.source_image_path, out_path)
        _verify_image(out_path)
        _normalize_registered_canvas(out_path, out_path, size, background=(255, 255, 255))
        ratio = quality_check(out_path, size)
        if not 0.001 <= ratio <= 0.5:
            logger.warning("scene {} extracted line-art foreground ratio {:.3f} looks unusual", scene.id, ratio)
        scene.lineart_path = out_path
        scene.lineart_source_fingerprint = source_fingerprint
        scene.image_path = out_path
    return scenes


def _assign_scene_image(scene: Scene, out_path: Path, asset_role: str) -> None:
    if asset_role == "source":
        scene.source_image_path = out_path
    else:
        scene.lineart_path = out_path
        scene.image_path = out_path


def _verify_image(path: Path) -> None:
    try:
        with Image.open(path) as image:
            image.verify()
    except Exception as exc:
        raise RuntimeError(f"Invalid generated image: {path}") from exc


def _normalize_registered_canvas(
    source_path: Path,
    out_path: Path,
    size: tuple[int, int],
    *,
    background: tuple[int, int, int] | None = None,
) -> None:
    """Place an asset on the project canvas without stretching or material cropping."""

    width, height = size
    if width <= 0 or height <= 0:
        raise ValueError("Storyboard canvas dimensions must be positive")
    with Image.open(source_path) as opened:
        source = opened.convert("RGB")
        source.load()
    if source.size == size and source_path == out_path:
        return
    source_ratio = source.width / source.height
    target_ratio = width / height
    ratio_difference = abs(source_ratio - target_ratio) / target_ratio
    if ratio_difference <= _NEAR_MATCHING_ASPECT_RATIO_TOLERANCE:
        canvas = ImageOps.fit(
            source,
            size,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        canvas.save(out_path, format="PNG")
        return

    if background is None:
        corners = (
            source.getpixel((0, 0)),
            source.getpixel((source.width - 1, 0)),
            source.getpixel((0, source.height - 1)),
            source.getpixel((source.width - 1, source.height - 1)),
        )
        background = tuple(round(sum(pixel[channel] for pixel in corners) / len(corners)) for channel in range(3))
    source = ImageOps.contain(source, size, method=Image.Resampling.LANCZOS)
    canvas = Image.new("RGB", size, background)
    canvas.paste(source, ((width - source.width) // 2, (height - source.height) // 2))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out_path, format="PNG")
