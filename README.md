# Whiteboard Video Engine

Local-first Python engine for stroke-by-stroke whiteboard videos.

This repository contains the reusable engine and CLI. The Codex skill adapter
lives in a separate repository:

```text
https://github.com/YOUR_ORG/codex-whiteboard-video-skill
```

Replace `YOUR_ORG` with your GitHub organization or username after publishing.

## What It Does

- Renders SVGs and line-art PNGs into smooth hand-drawn MP4 videos.
- Extracts local neural line art from uploaded photos or illustrations.
- Traces raster skeletons into drawable strokes.
- Merges tiny fragments into longer stroke paths for smoother hand movement.
- Supports fixed-orientation hand cursors.
- Supports short hand-drawn text with `--draw-text`.
- Fills color from the original source image after line drawing.

## Install

From a published repository:

```bash
python3 -m pip install "git+https://github.com/YOUR_ORG/whiteboard-video-engine.git"
```

For local development:

```bash
git clone https://github.com/YOUR_ORG/whiteboard-video-engine.git
cd whiteboard-video-engine
python3 -m venv .venv
. .venv/bin/activate
pip install -e ".[dev]"
```

If you are installing from a local checkout in a restricted network
environment, disable build isolation and install dependencies yourself first:

```bash
pip install numpy Pillow pydantic
pip install --no-build-isolation --no-deps -e .
```

Check the runtime:

```bash
whiteboard doctor
```

## Quick Start

Render a clean SVG:

```bash
whiteboard render-image tests/fixtures/apple.svg \
  -o out/apple.mp4 \
  --duration 2 \
  --fps 24 \
  --width 640 \
  --height 360 \
  --hand asian
```

Render an uploaded image with local neural line-art extraction:

```bash
whiteboard render-photo input.jpg \
  -o out/whiteboard.mp4 \
  --duration 15 \
  --fps 30 \
  --lineart-provider auto \
  --stroke-detail rich \
  --hand asian \
  --color-fill contour-wipe
```

If you do not install the console script:

```bash
PYTHONPATH=src python3 -m whiteboard_skill.cli render-image tests/fixtures/apple.svg \
  -o out/apple.mp4 \
  --duration 2 \
  --hand asian
```

## Local Line-Art Models

Model code and weights are not included in this repository. Users install them
locally from upstream projects.

Supported providers:

- Informative Drawings: best default for real photos and semantic line art.
- Anime2Sketch: best for anime, manga, illustration, and clean white-background
  art.

See [docs/MODELS.md](docs/MODELS.md).

## CLI Overview

```bash
whiteboard extract-lineart image.jpg -o lineart.png --provider auto
whiteboard render-photo image.jpg -o output.mp4 --duration 15 --lineart-provider auto
whiteboard render-image lineart.png -o output.mp4 --source-image image.jpg --size-from-image
whiteboard analyze-image lineart.png -o analysis.json --stroke-detail rich
whiteboard list-hands
whiteboard doctor
```

Common options:

- `--stroke-detail balanced|rich|max`
- `--hand asian|black|children|white|procedural|none`
- `--draw-text "Title"`
- `--color-fill contour-wipe|brush-scan|top-down-blocks|fade`
- `--lineart-snap-threshold 170`
- `--no-lineart-snap`

## Technical Stack

Core:

- Python
- Pillow
- NumPy
- Pydantic
- FFmpeg

Rendering:

- Zhang-Suen skeletonization
- 8-neighbor stroke tracing
- endpoint-based stroke merging
- long/medium/short stroke mix for smoother hand motion
- fixed-orientation PNG hand cursors
- contour-aware color fill

Optional model inference:

- PyTorch
- torchvision
- Informative Drawings
- Anime2Sketch
- optional VTracer CLI

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Codex Skill

Install the separate skill repository after installing this engine:

```bash
mkdir -p ~/.codex/skills
git clone https://github.com/YOUR_ORG/codex-whiteboard-video-skill.git \
  ~/.codex/skills/whiteboard-video
```

The skill wrapper imports this installed engine package. The skill repository
does not vendor `src/whiteboard_skill`.

## What Not To Commit

- `out/`, `work/`, generated videos, generated images
- `.venv/`, `.venv-lineart/`
- `tools/informative-drawings/`
- `tools/Anime2Sketch/`
- `*.pth`, `*.pt`, `*.ckpt`, `*.safetensors`, `*.onnx`, `*.bin`
- user uploads or copyrighted samples

## License

MIT. Check upstream model licenses separately.
