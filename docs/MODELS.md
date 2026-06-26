# Local Line-Art Model Setup

The engine does not ship neural model repositories or model weights. `render-photo` and `extract-lineart` discover local providers from the current working directory.

Use one project directory as the runtime root:

```text
my-whiteboard-project/
  .venv-lineart/
    bin/
      python
  tools/
    lineart/
      run_informative_drawings.py
      run_anime2sketch.py
    informative-drawings/
      checkpoints/
        model/
          anime_style/
            netG_A_latest.pth
          contour_style/
            netG_A_latest.pth        # optional
          opensketch_style/
            netG_A_latest.pth        # optional
    Anime2Sketch/
      weights/
        netG.pth
        improved.bin                 # optional; preferred when available
```

`tools/lineart/*.py` are the wrapper scripts from this repository. Model repositories and weights are downloaded from upstream projects.

## Runtime Environment

Create a separate environment for PyTorch model inference:

```bash
python3 -m venv .venv-lineart
.venv-lineart/bin/python -m pip install --upgrade pip setuptools wheel
.venv-lineart/bin/python -m pip install torch torchvision Pillow numpy tqdm
```

When `.venv-lineart/bin/python` exists under the runtime root, the engine uses it automatically.

## Wrapper Scripts

Keep these files in:

```text
tools/lineart/
  run_informative_drawings.py
  run_anime2sketch.py
```

If you installed the engine with `pip` and do not have a repository checkout, copy the wrapper scripts from:

```text
https://github.com/gnipbao/whiteboard-video-engine/tree/main/tools/lineart
```

## Informative Drawings

Recommended for photos and semantic line art.

Upstream:

- GitHub: https://github.com/carolineec/informative-drawings
- Project page: https://carolineec.github.io/informative_drawings/
- Paper: https://arxiv.org/abs/2203.12691
- Demo: https://huggingface.co/spaces/carolineec/informativedrawings

Install code:

```bash
mkdir -p tools
git clone https://github.com/carolineec/informative-drawings.git tools/informative-drawings
```

Download pretrained weights from the upstream README:

```text
https://drive.google.com/file/d/1MIdHzecxz-z0uY3ARL_R40DlKcuQxiDk/view?usp=sharing
```

Expected weight path, either layout is valid:

```text
tools/informative-drawings/checkpoints/anime_style/netG_A_latest.pth
tools/informative-drawings/checkpoints/model/anime_style/netG_A_latest.pth
```

Optional styles can use:

```text
tools/informative-drawings/checkpoints/model/contour_style/netG_A_latest.pth
tools/informative-drawings/checkpoints/model/opensketch_style/netG_A_latest.pth
```

Manual test:

```bash
.venv-lineart/bin/python tools/lineart/run_informative_drawings.py \
  input.jpg \
  out/lineart-informative.png \
  --style anime_style \
  --size 768
```

## Anime2Sketch

Recommended for anime, manga, illustration, and clean white-background art.

Upstream:

- GitHub: https://github.com/Mukosame/Anime2Sketch
- Paper reference in upstream README: https://arxiv.org/abs/2104.05703

Install code:

```bash
mkdir -p tools
git clone https://github.com/Mukosame/Anime2Sketch.git tools/Anime2Sketch
```

Download weights from the upstream README:

```text
Default weights folder:
https://drive.google.com/drive/folders/1Srf-WYUixK0wiUddc9y3pNKHHno5PN6R?usp=sharing

Artifact-free weight for dark / low-contrast images:
https://drive.google.com/file/d/1cf90_fPW-elGOKu5mTXT5N1dum-XY_46/view?usp=sharing
```

Expected weight path:

```text
tools/Anime2Sketch/weights/netG.pth
tools/Anime2Sketch/weights/improved.bin
```

If `improved.bin` exists, the wrapper uses it first. Otherwise it uses `netG.pth`.

Manual test:

```bash
.venv-lineart/bin/python tools/lineart/run_anime2sketch.py \
  input.png \
  out/lineart-anime2sketch.png \
  --load-size 768
```

## Auto-Discovery Rules

Run `whiteboard` from the runtime root that contains `tools/`:

```bash
cd my-whiteboard-project
whiteboard extract-lineart input.jpg -o out/lineart.png --provider auto
```

Provider order for `--provider auto`:

1. Informative Drawings, when wrapper and weights are present.
2. Anime2Sketch, when wrapper and weights are present.
3. Fail with an explicit setup error.

There is no Canny, XDoG, or edge-only production fallback.

## Explicit Command Mode

If model folders are outside the current working directory, set commands explicitly:

```bash
export WHITEBOARD_INFORMATIVE_DRAWINGS_CMD="/abs/project/.venv-lineart/bin/python /abs/project/tools/lineart/run_informative_drawings.py {input} {output}"
export WHITEBOARD_ANIME2SKETCH_CMD="/abs/project/.venv-lineart/bin/python /abs/project/tools/lineart/run_anime2sketch.py {input} {output}"
```

Commands may contain `{input}` and `{output}` placeholders. If placeholders are omitted, the engine appends input and output paths as positional arguments.

## Version-Control Policy

Commit:

```text
tools/lineart/run_informative_drawings.py
tools/lineart/run_anime2sketch.py
```

Do not commit:

```text
tools/informative-drawings/
tools/Anime2Sketch/
.venv-lineart/
*.pth
*.pt
*.ckpt
*.safetensors
*.onnx
*.bin
```
