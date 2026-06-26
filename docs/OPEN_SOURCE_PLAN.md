# Open-Source Plan

## Repository Split

Use two public repositories:

```text
whiteboard-video-engine
  Python package, CLI, renderer, model wrapper scripts, tests, docs

codex-whiteboard-video-skill
  Codex SKILL.md, references, examples, tiny wrapper script
```

The skill repository depends on this engine. It should not duplicate engine
source code.

## Why Split Now

This project has two audiences:

- normal Python/CLI users
- Codex users who install skills

Keeping the engine separate makes installation clearer:

```bash
python3 -m pip install "git+https://github.com/gnipbao/whiteboard-video-engine.git"
git clone https://github.com/gnipbao/codex-whiteboard-video-skill.git ~/.codex/skills/whiteboard-video
```

The skill can evolve as prompt/instruction packaging, while the engine can
evolve as a Python package.

## Public Engine Surface

Treat these as stable or semi-stable:

- package import: `whiteboard_skill`
- CLI entrypoint: `whiteboard`
- commands:
  - `extract-lineart`
  - `render-photo`
  - `render-image`
  - `analyze-image`
  - `compose`
  - `doctor`
- provider env vars:
  - `WHITEBOARD_INFORMATIVE_DRAWINGS_CMD`
  - `WHITEBOARD_ANIME2SKETCH_CMD`
  - `WHITEBOARD_LINEART_PYTHON`

Treat these as internal:

- stroke merge heuristics
- dataclass fields
- temporary frame layout
- line-art provider readiness helpers

## Engine Repository Should Contain

- `src/`
- `tests/`
- `examples/`
- `assets/hands/`
- `tools/lineart/`
- `docs/`
- `README.md`
- `pyproject.toml`
- `Makefile`
- `.env.example`
- `.gitignore`

## Engine Repository Must Not Contain

- third-party model repositories
- model weights
- user uploads
- generated videos, except small curated demos under `examples/cases/`
- local virtualenvs
- Codex skill vendor copy of engine source

## Skill Repository Should Contain

- `SKILL.md`
- `README.md`
- `scripts/whiteboard_cli.py`
- `references/`
- `examples/`
- optional `agents/`

## Skill Repository Must Not Contain

- `src/whiteboard_skill`
- model repos
- model weights
- generated videos
- local virtualenvs

## Release Checklist

Before publishing:

1. Add a license to both repositories.
2. Confirm repository URLs point to `https://github.com/gnipbao/`.
3. Confirm `.gitignore` excludes weights, model repos, outputs, and virtualenvs.
4. Run engine tests without model weights.
5. Run one local model smoke test manually and document the result.
6. Install the skill from the skill repository and verify it can call the
   installed engine.
7. Confirm demo assets under `examples/cases/` are owned by the project or have
   explicit open-source distribution permission.

## Future Packaging

The engine can later be published to PyPI:

```bash
pip install whiteboard-video-engine
```

When that happens, update the skill install docs to prefer the PyPI package and
keep the GitHub install as a development option.
