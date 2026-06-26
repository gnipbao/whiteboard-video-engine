# Sports Illustration, Anime2Sketch, Long-Stroke Mix

中文 | [English](#english)

这是一个 15 秒插画转白板手绘视频案例，用于展示 Anime2Sketch 线稿提取、长短 stroke 混合、固定手势平移和轮廓感上色。

## 文件

- `input.jpg`：输入插画。
- `output.mp4`：示例输出视频。

## 推荐命令

```bash
whiteboard render-photo examples/cases/sports-illustration-anime2sketch/input.jpg \
  -o out/sports-illustration-anime2sketch-longmix-15s.mp4 \
  --duration 15 \
  --fps 30 \
  --lineart-provider anime2sketch \
  --stroke-detail rich \
  --hand asian \
  --tail-color 4.5 \
  --color-fill contour-wipe
```

## 适用场景

- 白底动漫或插画图。
- 主体轮廓清晰，但内部服装、头发、鞋带等细节需要保留。
- 希望减少碎短线，让手绘路径更接近一笔一笔画出来的感觉。

## 说明

如果公开发布本仓库，请确认 `input.jpg` 和 `output.mp4` 的素材分发权。没有明确授权时，建议换成自有素材或开放许可素材。

---

## English

This is a 15-second illustration-to-whiteboard example case. It demonstrates Anime2Sketch line-art extraction, mixed long/short stroke rendering, fixed-orientation hand translation, and contour-aware color fill.

### Files

- `input.jpg`: source illustration.
- `output.mp4`: rendered demo video.

### Command

```bash
whiteboard render-photo examples/cases/sports-illustration-anime2sketch/input.jpg \
  -o out/sports-illustration-anime2sketch-longmix-15s.mp4 \
  --duration 15 \
  --fps 30 \
  --lineart-provider anime2sketch \
  --stroke-detail rich \
  --hand asian \
  --tail-color 4.5 \
  --color-fill contour-wipe
```

### Best For

- Clean anime or illustration images on a white background.
- Strong subject outlines with meaningful inner details.
- Outputs where long strokes should be preserved while still keeping enough fine detail.

### License Note

Before publishing this repository, make sure the demo source image and output video can be redistributed under the repository license. Replace them with owned or openly licensed assets when needed.
