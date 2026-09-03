# Visual Style Sources / 视觉风格来源

The visual-style registry contains prompt text and bounded renderer parameters.
It does not bundle or fetch third-party sample artwork, artist reference boards,
brush packs, texture files, fonts, model weights, or code from drawing tools.

视觉风格注册表只包含文字配方和受限的渲染参数，不内嵌或在线获取第三方
样图、艺术家参考板、笔刷包、纹理、字体、模型权重或绘图工具代码。

## Naming policy / 命名原则

Built-in names describe observable media and production methods, for example
`colored-pencil-diary`, `clean-whiteboard`, `ink-wash-minimal`, and
`linocut-editorial`. They are not artist names and are not promises to reproduce
the unique style of a particular living artist. Custom recipes should follow the
same rule: describe line weight, material, paper, palette, geometry, texture,
negative space, and composition instead of naming an artist.

内置名称只描述可观察的媒介与制作方法，例如彩铅、白板笔、水墨留白和木刻。
它们不使用艺术家姓名，也不表示能够复制某位在世艺术家的独特风格。自定义
配方也应描述线重、画材、纸面、色板、几何、肌理、留白与构图，不要以艺术家
姓名代替视觉说明。

## Provenance / 来源

| Recipe group | Styles | Source note |
| --- | --- | --- |
| Engine-curated media recipes | `warm-crayon-storybook`, `clean-whiteboard`, `marker-whiteboard`, `semantic-ink`, `anime-graphite`, `notebook-pencil-doodle`, `ink-wash-minimal`, `manga-screentone`, `blueprint-pencil`, `editorial-portrait` | Written for this engine from generic media terminology and its extraction/rendering constraints. |
| Adapted prompt recipes | `colored-pencil-diary`, `minimal-line-explainer`, `kid-crayon`, `raw-kid-crayon`, `bean-doodle-infographic`, `organic-contour-doodle`, `naive-marker-notes`, `ballpoint-scribble`, `inked-storybook`, `emotional-watercolor-sketch`, `retro-gouache-concept`, `nordic-gouache-storybook`, `sunlit-storybook`, `warm-flat-storybook`, `zine-riso-collage`, `linocut-editorial`, `ms-paint-doodle`, `real-crayon-paper` | Rewritten and adapted from the MIT-licensed [story-to-handdrawn-video](https://github.com/gnipbao/story-to-handdrawn-video) catalog, which retains attribution to [threerocks/hand-drawn-styles](https://github.com/threerocks/hand-drawn-styles). No reference images or brush assets were copied into this engine. |
| Rough diagram language | `rough-diagram` | Media-language study informed by MIT-licensed [Excalidraw](https://github.com/excalidraw/excalidraw) and [Rough.js](https://github.com/rough-stuff/rough); no upstream code or assets embedded. |
| Pressure-line language | `pressure-ink-notes` | Media-language study informed by MIT-licensed [perfect-freehand](https://github.com/steveruizok/perfect-freehand); no upstream code or assets embedded. |

The retained upstream license notice is included in
[`THIRD_PARTY_NOTICES.md`](../THIRD_PARTY_NOTICES.md).

Informative Drawings and Anime2Sketch are optional line-art providers, not
visual-style assets. Their code and weights keep their own licenses; see
[MODELS.md](MODELS.md).

## Compatibility levels / 白板适配等级

- `native`: the requested contours and color regions normally survive local
  line extraction and work well with stroke drawing and semantic object blocks.
- `adaptive`: the recipe selects softer, dry-brush, or denser detail settings;
  previewing is recommended.
- `experimental`: deliberate pixel jaggies, dense scribbles, halftones, black
  masses, collage, or near-outline-free shapes can produce unstable skeletons.

The level describes compatibility with this renderer, not artistic quality.
Run `whiteboard list-styles --compatibility native` for the most predictable
production choices.

适配等级只描述风格与当前抽线、骨架和分块渲染器的关系，不评价艺术质量。
正式批量生产优先使用 `native`；`adaptive` 和 `experimental` 建议先渲染短预览。

## Custom recipes / 自定义配方

An inline description uses safe defaults:

```bash
whiteboard run story.md -o out/story.mp4 \
  --custom-style "Loose blue-pencil travel sketch, one warm-orange accent, broad white space"
```

For repeatable work, use a UTF-8 JSON object and inherit a built-in recipe with
`extends`:

```bash
whiteboard run story.md -o out/story.mp4 \
  --custom-style-file examples/custom-style.example.json
```

Supported top-level fields are:

```text
extends, schema_version, id, order, name_zh, name_en, family, summary,
best_for, aliases, compatibility, planner_guidance, aesthetic, paper,
palette, avoid, render
```

`render` accepts only maintained controls:

| Field | Accepted values |
| --- | --- |
| `block_fill_style` | `crayon`, `clean`, `soft-wash`, `dry-brush` |
| `color_fill_scope` | `block` or `scene` |
| `stroke_detail` | `balanced`, `rich`, `max` |
| `line_thickness` | integer `0..16`; `0` keeps automatic sizing |
| `line_art_snap` | boolean |
| `line_art_snap_threshold` | integer `1..254` |
| `max_draw_blocks` | integer `1..24` |
| `draw_blocks` | integer `1..24` or `null` |
| `block_overlap` | number `0..0.65` |
| `block_order` | `reading` or `source` |

`block` keeps the established object-local sequence: coarse contour, detail,
then color inside each natural block. `scene` leaves the coarse/detail grouping
unchanged but defers color to one registered full-frame left-to-right pass. Use
`scene` when the generated art has a continuous full-bleed environment; it
prevents low-saturation sky, snow, walls, or washes from being revealed as
rectangular object regions. The built-in style 9, `anime-graphite`, defaults to
`scene`; the other built-ins keep their declared/default scope.

For a recipe that uses `scene`, its planner guidance should request one
continuous, low-detail background and explicitly avoid panels, page frames,
rectangular scenic cutouts, or disconnected backdrop islands. Foreground people
and props must still be complete and spatially readable because their line art
continues to use natural object grouping. The default timing reserves roughly
the first 72% of the drawing interval for those block lines and begins the
whole-scene color pass near 68%, creating about 4% detail/color overlap.

Unknown keys are rejected. A custom style file is limited to 64 KiB and an
inline description to 4,000 characters. If `extends` is omitted, the JSON
inherits `warm-crayon-storybook`. `--style`, `--custom-style`, and
`--custom-style-file` are mutually exclusive.

Custom recipes cannot claim an upstream provenance value. The engine marks
them as user-authored metadata in CLI output and project snapshots. An explicit
custom `id` must start with `custom-`, so it cannot impersonate a built-in id.

The engine appends its production contract after every built-in or custom
recipe. Custom text cannot disable complete-subject framing, extractable
outlines, natural object grouping, negative space, or the prohibition on
generated captions, pseudo-writing, logos, watermarks, and subtitle bands.
Use `--theme` for one story's mood or art direction without creating a new
style recipe.
