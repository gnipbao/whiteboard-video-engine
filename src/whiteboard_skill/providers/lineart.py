"""Local image-to-line-art providers.

The production path is explicit: use a locally installed neural extractor and
fail fast when no model-backed provider is available. This avoids silently
falling back to weak edge detection for uploaded photos.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from abc import ABC, abstractmethod
from pathlib import Path

import numpy as np
from PIL import Image


class LineArtProvider(ABC):
    """Extract a black-on-white line-art bitmap from a color image."""

    @abstractmethod
    def extract(self, color_png: Path, out_png: Path) -> Path:
        """Write line art to `out_png` and return it."""


class ExternalCommandLineArt(LineArtProvider):
    """Line-art provider backed by an external command.

    The command may contain `{input}` and `{output}` placeholders. If it does
    not, the input and output paths are appended as positional arguments.
    """

    def __init__(self, command: str, name: str) -> None:
        self.command = command
        self.name = name

    def extract(self, color_png: Path, out_png: Path) -> Path:
        out_png.parent.mkdir(parents=True, exist_ok=True)
        if "{input}" in self.command or "{output}" in self.command:
            args = [
                arg.replace("{input}", str(color_png)).replace("{output}", str(out_png))
                for arg in shlex.split(self.command)
            ]
        else:
            args = [*shlex.split(self.command), str(color_png), str(out_png)]
        subprocess.run(args, check=True)
        if not out_png.exists():
            raise RuntimeError(f"{self.name} did not create output: {out_png}")
        _postprocess_extracted_lineart(out_png, self.name)
        return out_png


class InformativeDrawingsLineArt(ExternalCommandLineArt):
    """Wrapper for a locally installed Informative-Drawings extractor."""

    def __init__(self, command: str) -> None:
        super().__init__(command, "informative-drawings")


class Anime2SketchLineArt(ExternalCommandLineArt):
    """Wrapper for a locally installed Anime2Sketch extractor."""

    def __init__(self, command: str) -> None:
        super().__init__(command, "anime2sketch")


def get_lineart_provider(name: str = "auto") -> LineArtProvider:
    """Return a configured local line-art provider."""

    normalized = name.lower().strip()
    if normalized == "auto":
        informative = _command_from_env_path_or_local_wrapper(
            "WHITEBOARD_INFORMATIVE_DRAWINGS_CMD",
            "informative-drawings",
            "run_informative_drawings.py",
            _informative_weights_ready,
        )
        if informative:
            return InformativeDrawingsLineArt(informative)
        anime = _command_from_env_path_or_local_wrapper(
            "WHITEBOARD_ANIME2SKETCH_CMD",
            "anime2sketch-lineart",
            "run_anime2sketch.py",
            _anime2sketch_weights_ready,
        )
        if anime:
            return Anime2SketchLineArt(anime)
        raise RuntimeError(
            "No neural line-art provider is installed. Install Informative Drawings weights "
            "at tools/informative-drawings/checkpoints/anime_style/netG_A_latest.pth "
            "or tools/informative-drawings/checkpoints/model/anime_style/netG_A_latest.pth; install "
            "Anime2Sketch weights at tools/Anime2Sketch/weights/netG.pth / improved.bin. "
            "You can also set WHITEBOARD_INFORMATIVE_DRAWINGS_CMD or WHITEBOARD_ANIME2SKETCH_CMD."
        )
    if normalized in {"informative", "informative-drawings"}:
        command = _command_from_env_path_or_local_wrapper(
            "WHITEBOARD_INFORMATIVE_DRAWINGS_CMD",
            "informative-drawings",
            "run_informative_drawings.py",
            _informative_weights_ready,
            strict_local=True,
        )
        if not command:
            raise RuntimeError("Informative-Drawings command not found. Set WHITEBOARD_INFORMATIVE_DRAWINGS_CMD.")
        return InformativeDrawingsLineArt(command)
    if normalized in {"anime", "anime2sketch", "manga", "manga-line", "manga-line-extraction"}:
        command = _command_from_env_path_or_local_wrapper(
            "WHITEBOARD_ANIME2SKETCH_CMD",
            "anime2sketch-lineart",
            "run_anime2sketch.py",
            _anime2sketch_weights_ready,
            strict_local=True,
        )
        if not command:
            raise RuntimeError("Anime2Sketch command not found. Set WHITEBOARD_ANIME2SKETCH_CMD.")
        return Anime2SketchLineArt(command)
    raise ValueError(f"Unknown line-art provider: {name}")


def vectorize_with_vtracer(line_png: Path, svg_path: Path) -> Path:
    """Vectorize a line-art bitmap with vtracer when the CLI is installed."""

    executable = shutil.which("vtracer")
    if not executable:
        raise RuntimeError("vtracer CLI not found on PATH")
    svg_path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            executable,
            "--input",
            str(line_png),
            "--output",
            str(svg_path),
            "--mode",
            "polygon",
            "--filter_speckle",
            "4",
            "--color_precision",
            "1",
        ],
        check=True,
    )
    return svg_path


def _command_from_env_path_or_local_wrapper(
    env_name: str,
    executable: str,
    wrapper_name: str,
    readiness_check,
    strict_local: bool = False,
) -> str | None:
    value = os.getenv(env_name)
    if value:
        return value
    found = shutil.which(executable)
    if found:
        return found
    wrapper = _find_local_wrapper(wrapper_name)
    if wrapper and readiness_check(wrapper):
        python = _lineart_python(wrapper)
        return f"{shlex.quote(str(python))} {shlex.quote(str(wrapper))} {{input}} {{output}}"
    if strict_local and wrapper:
        return None
    return None


def _find_local_wrapper(wrapper_name: str) -> Path | None:
    search_roots = [Path.cwd()]
    module_root = Path(__file__).resolve()
    search_roots.extend(module_root.parents)
    for root in search_roots:
        candidate = root / "tools" / "lineart" / wrapper_name
        if candidate.exists():
            return candidate
    return None


def _lineart_python(wrapper: Path) -> Path:
    for root in [wrapper.parents[2], *wrapper.parents]:
        candidate = root / ".venv-lineart" / "bin" / "python"
        if candidate.exists():
            return candidate
    return Path(os.getenv("WHITEBOARD_LINEART_PYTHON", "python3"))


def _informative_weights_ready(wrapper: Path) -> bool:
    root = wrapper.parents[1] / "informative-drawings"
    return (
        (root / "checkpoints" / "anime_style" / "netG_A_latest.pth").exists()
        or (root / "checkpoints" / "model" / "anime_style" / "netG_A_latest.pth").exists()
    )


def _anime2sketch_weights_ready(wrapper: Path) -> bool:
    root = wrapper.parents[1] / "Anime2Sketch"
    weights = root / "weights"
    return (weights / "netG.pth").exists() or (weights / "improved.bin").exists()


def _postprocess_extracted_lineart(image_path: Path, provider_name: str) -> None:
    """Convert neural pencil output into cleaner whiteboard line art.

    Anime2Sketch often emits many very light gray pencil fragments. Those are
    useful for still sketches, but they become noisy short strokes after
    skeleton tracing. The default cleanup keeps darker semantic contours,
    removes tiny isolated components, and writes pure black-on-white output.
    """

    params = _lineart_cleanup_params(provider_name)
    if params is None:
        _clean_canvas_edges(image_path)
        return

    image = Image.open(image_path).convert("L")
    gray = np.asarray(image, dtype=np.uint8)
    mask = gray < int(params["threshold"])
    mask = _suppress_canvas_border_mask(mask)
    mask = _remove_small_ink_components(
        mask,
        min_area=max(10, int(round(min(mask.shape) * float(params["min_area_ratio"])))),
        min_span=max(8, int(round(min(mask.shape) * float(params["min_span_ratio"])))),
    )
    dilation = int(params["dilation"])
    if dilation > 0:
        mask = _binary_dilate(mask, iterations=dilation)
        mask = _suppress_canvas_border_mask(mask)
    final = np.where(mask, 0, 255).astype(np.uint8)
    Image.fromarray(final, mode="L").convert("RGB").save(image_path)


def _lineart_cleanup_params(provider_name: str) -> dict[str, float | int] | None:
    mode = os.getenv("WHITEBOARD_LINEART_CLEANUP", "auto").strip().lower()
    if mode in {"0", "false", "off", "none", "raw"}:
        return None

    normalized = provider_name.lower()
    presets: dict[str, dict[str, float | int]] = {
        "soft": {"threshold": 235, "min_area_ratio": 0.007, "min_span_ratio": 0.006, "dilation": 0},
        "balanced": {"threshold": 224, "min_area_ratio": 0.011, "min_span_ratio": 0.008, "dilation": 0},
        "strong": {"threshold": 216, "min_area_ratio": 0.018, "min_span_ratio": 0.012, "dilation": 1},
    }
    if mode == "auto":
        params = presets["balanced" if "anime2sketch" in normalized else "soft"].copy()
    else:
        if mode not in presets:
            raise RuntimeError(f"Unknown WHITEBOARD_LINEART_CLEANUP mode: {mode}")
        params = presets[mode].copy()

    env_prefix = "WHITEBOARD_ANIME2SKETCH" if "anime2sketch" in normalized else "WHITEBOARD_LINEART"
    params["threshold"] = int(os.getenv(f"{env_prefix}_CLEAN_THRESHOLD", str(params["threshold"])))
    params["dilation"] = int(os.getenv(f"{env_prefix}_CLEAN_DILATION", str(params["dilation"])))
    return params


def _suppress_canvas_border_mask(mask: np.ndarray) -> np.ndarray:
    from ..preprocess import suppress_canvas_border

    return suppress_canvas_border(mask)


def _clean_canvas_edges(image_path: Path, threshold: int = 250) -> None:
    image = Image.open(image_path).convert("RGB")
    gray = np.asarray(image.convert("L"), dtype=np.uint8)
    ink = gray < threshold
    cleaned = _suppress_canvas_border_mask(ink)
    if np.array_equal(ink, cleaned):
        return
    arr = np.asarray(image, dtype=np.uint8).copy()
    arr[ink & ~cleaned] = 255
    Image.fromarray(arr, mode="RGB").save(image_path)


def _binary_dilate(mask: np.ndarray, iterations: int = 1) -> np.ndarray:
    result = mask.astype(bool)
    for _ in range(max(0, iterations)):
        padded = np.pad(result, 1, mode="constant", constant_values=False)
        result = np.zeros_like(result, dtype=bool)
        for y_offset in range(3):
            for x_offset in range(3):
                result |= padded[y_offset : y_offset + mask.shape[0], x_offset : x_offset + mask.shape[1]]
    return result


def _remove_small_ink_components(mask: np.ndarray, min_area: int, min_span: int) -> np.ndarray:
    if not np.any(mask):
        return mask
    height, width = mask.shape
    visited = np.zeros_like(mask, dtype=bool)
    keep = np.zeros_like(mask, dtype=bool)
    ys, xs = np.nonzero(mask)
    for start_y, start_x in zip(ys.tolist(), xs.tolist()):
        if visited[start_y, start_x]:
            continue
        stack = [(start_x, start_y)]
        visited[start_y, start_x] = True
        coords: list[tuple[int, int]] = []
        min_x = max_x = start_x
        min_y = max_y = start_y
        while stack:
            x, y = stack.pop()
            coords.append((x, y))
            min_x, max_x = min(min_x, x), max(max_x, x)
            min_y, max_y = min(min_y, y), max(max_y, y)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx == 0 and dy == 0:
                        continue
                    nx, ny = x + dx, y + dy
                    if 0 <= nx < width and 0 <= ny < height and mask[ny, nx] and not visited[ny, nx]:
                        visited[ny, nx] = True
                        stack.append((nx, ny))
        span = max(max_x - min_x + 1, max_y - min_y + 1)
        if len(coords) >= min_area or span >= min_span:
            for x, y in coords:
                keep[y, x] = True
    return keep
