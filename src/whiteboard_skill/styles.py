"""Versioned visual-style recipes for storyboard generation and rendering."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator

DEFAULT_STYLE_ID = "warm-crayon-storybook"
STYLE_PROMPT_MARKER = "[WHITEBOARD_VISUAL_STYLE]"
STORYBOARD_PROMPT_SCHEMA = 2
MAX_CUSTOM_STYLE_BYTES = 65_536

LEGACY_STYLE_SUFFIX = (
    ", warm hand-drawn storybook illustration on clean off-white paper, "
    "expressive characters and meaningful props, simple readable composition, "
    "two to four visually separated object clusters when the scene has multiple beats, "
    "clear white space between clusters and no long ground line connecting them, "
    "clean dark crayon-and-pencil outlines, flat crayon colors, subtle wax texture, "
    "clear separated color regions, natural breathing room around subjects for optional tiny labels, "
    "no text, no letters, no numbers, no logo, no watermark, no picture frame"
)

_PRODUCTION_CONTRACT = (
    "Show one immediately readable story beat. Keep every person and important prop complete "
    "inside the canvas with breathing room; never crop heads, hands, feet, or key objects. "
    "Use two to four spatially separate clusters only when the story contains genuinely independent "
    "objects or beats, and never cut one connected person or object into artificial blocks. "
    "Keep outlines distinguishable from fills so a local line-art extractor can recover the drawing. "
    "Leave useful negative space and avoid a continuous ground line joining the entire canvas. "
    "Generate image only: no text, letters, numbers, pseudo-writing, captions, logo, watermark, "
    "panel border, page frame, UI, or subtitle band."
)

_WHOLE_SCENE_BACKGROUND_CONTRACT = (
    "Render buildings, walls, sky, ground, and ambient scenery as one coherent "
    "low-contrast background plane spanning the composition. Keep foreground subjects "
    "complete, darker, and silhouette-separated. Only genuinely independent foreground "
    "story beats may become separate clusters. Never turn scenery into floating cards, "
    "panels, vignettes, or hard-edged rectangular islands."
)


class RenderStyle(BaseModel):
    """Only style-controlled parameters that affect the maintained renderer."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    block_fill_style: Literal["crayon", "clean", "soft-wash", "dry-brush"] = "crayon"
    color_fill_scope: Literal["block", "scene"] = "block"
    stroke_detail: Literal["balanced", "rich", "max"] = "rich"
    line_thickness: int = Field(default=0, ge=0, le=16)
    line_art_snap: bool = True
    line_art_snap_threshold: int = Field(default=235, ge=1, le=254)
    max_draw_blocks: int = Field(default=6, ge=1, le=24)
    draw_blocks: int | None = Field(default=4, ge=1, le=24)
    block_overlap: float = Field(default=0.16, ge=0.0, le=0.65)
    block_order: Literal["reading", "source"] = "reading"


class VisualStyle(BaseModel):
    """Resolved, immutable storyboard and render recipe."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal[1] = 1
    id: str
    order: int = Field(default=0, ge=0)
    name_zh: str
    name_en: str
    family: str
    summary: str
    best_for: tuple[str, ...] = ()
    aliases: tuple[str, ...] = ()
    compatibility: Literal["native", "adaptive", "experimental"] = "native"
    planner_guidance: str
    aesthetic: str
    paper: str
    palette: str
    avoid: str
    render: RenderStyle = Field(default_factory=RenderStyle)
    provenance: str = "whiteboard-video-engine"

    @field_validator(
        "id",
        "name_zh",
        "name_en",
        "family",
        "summary",
        "planner_guidance",
        "aesthetic",
        "paper",
        "palette",
        "avoid",
        "provenance",
    )
    @classmethod
    def validate_text(cls, value: str, info: ValidationInfo) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError(f"{info.field_name} cannot be empty")
        if info.field_name == "id" and not re.fullmatch(
            r"[a-z0-9][a-z0-9-]{1,63}", cleaned
        ):
            raise ValueError(
                "style id must contain lowercase letters, digits, and hyphens"
            )
        return cleaned


def _render(**overrides: object) -> RenderStyle:
    return RenderStyle.model_validate({**RenderStyle().model_dump(), **overrides})


def _style(
    style_id: str,
    order: int,
    name_zh: str,
    name_en: str,
    family: str,
    summary: str,
    best_for: tuple[str, ...],
    aliases: tuple[str, ...],
    compatibility: Literal["native", "adaptive", "experimental"],
    planner_guidance: str,
    aesthetic: str,
    paper: str,
    palette: str,
    avoid: str,
    *,
    render: RenderStyle | None = None,
    provenance: str = "whiteboard-video-engine curated media recipe",
) -> VisualStyle:
    return VisualStyle(
        id=style_id,
        order=order,
        name_zh=name_zh,
        name_en=name_en,
        family=family,
        summary=summary,
        best_for=best_for,
        aliases=aliases,
        compatibility=compatibility,
        planner_guidance=planner_guidance,
        aesthetic=aesthetic,
        paper=paper,
        palette=palette,
        avoid=avoid,
        render=render or RenderStyle(),
        provenance=provenance,
    )


BUILTIN_STYLES: tuple[VisualStyle, ...] = (
    _style(
        "warm-crayon-storybook",
        1,
        "暖色蜡笔故事书（默认）",
        "Warm crayon storybook",
        "蜡笔绘本",
        "清晰深色轮廓、温暖蜡笔平涂和充足留白，最适合故事分镜逐层绘制。",
        ("寓言", "儿童故事", "生活故事", "知识启蒙"),
        ("default", "storybook-crayon", "warm-crayon", "暖色蜡笔"),
        "native",
        "Favor expressive characters, meaningful props, simple silhouettes, and natural object separation.",
        "Warm hand-drawn storybook illustration with clean dark crayon-and-pencil outlines, flat handmade color regions, and subtle wax texture.",
        "Clean warm off-white drawing paper with no photographed tabletop or border.",
        "Muted brick red, dusty blue, ochre, sage, tan, charcoal, and warm cream; visible but restrained wax strokes.",
        "glossy digital painting, smooth vector fills, anime polish, realistic lighting, dense scenery, gradients",
    ),
    _style(
        "colored-pencil-diary",
        2,
        "彩铅日记漫画",
        "Colored-pencil diary comic",
        "彩铅叙事",
        "笨拙毡尖轮廓配低饱和彩铅短笔触，像亲手记录的生活日记。",
        ("家庭", "纪实", "情感", "日常"),
        ("diary", "colored-pencil", "彩铅日记"),
        "native",
        "Stage the scene like one candid diary memory with compact figures and only essential props.",
        "Naive black felt-tip contours, oversized rounded heads, compact bodies, simple expressive faces, and visibly layered colored-pencil strokes with white gaps.",
        "Bright clean white page with abundant untouched space and almost no paper grain.",
        "Dusty blue, brick red, charcoal, beige, tan, muted yellow, and light gray in low-saturation pencil layers.",
        "anime, vector cleanliness, smooth fills, watercolor wash, gradients, realistic lighting, detailed rooms",
        render=_render(
            block_fill_style="dry-brush", line_thickness=0, stroke_detail="rich"
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "clean-whiteboard",
        3,
        "经典清爽白板",
        "Clean whiteboard",
        "白板讲解",
        "黑色圆头白板笔和极少彩色重点，结构最清楚、抽线最稳定。",
        ("教程", "商业解释", "流程", "时间线"),
        ("whiteboard", "classic-whiteboard", "whiteboard-explainer", "经典白板"),
        "native",
        "Prioritize cause-and-effect, readable gestures, simple arrows, and diagram-like spacing.",
        "Freshly hand-drawn whiteboard illustration using medium black round-tip marker contours, simple people and props, and only essential arrows or circles.",
        "Clean matte whiteboard-white background without glare, room photography, borders, or smudged eraser bands.",
        "Black marker with at most one blue and one red emphasis; most regions stay white.",
        "filled color masses, shadows, complex scenery, corporate icon libraries, dense annotations, pseudo-text",
        render=_render(
            block_fill_style="clean",
            stroke_detail="balanced",
            line_thickness=0,
            draw_blocks=4,
        ),
    ),
    _style(
        "minimal-line-explainer",
        4,
        "极简黑白线条讲解",
        "Minimal line explainer",
        "白板讲解",
        "火柴人与极少道具构成的纯黑白解释图，信息读取速度最快。",
        ("观点", "步骤", "科普", "概念"),
        ("minimal-line", "stickman", "火柴人", "极简线条"),
        "native",
        "Reduce every beat to one unmistakable gesture and one identity prop.",
        "Minimal black-ink explainer drawing with circle-headed stick figures, dot-line faces, thin slightly wobbly contours, and flat two-dimensional geometry.",
        "Plain ivory-white paper with very large negative space.",
        "Pure black line on ivory paper; no gray and no colored fill.",
        "solid black silhouettes, hatching, shading, volume, thick outlines, realism, decoration",
        render=_render(
            block_fill_style="clean",
            stroke_detail="balanced",
            line_thickness=1,
            max_draw_blocks=5,
            draw_blocks=4,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "marker-whiteboard",
        5,
        "粗马克笔白板",
        "Bold marker whiteboard",
        "白板讲解",
        "粗细略变的白板笔、强动作剪影和局部荧光标记，适合快速短视频。",
        ("短视频", "营销解释", "清单", "强观点"),
        ("bold-whiteboard", "marker-board", "马克笔白板"),
        "native",
        "Use bold readable poses and no more than three large visual ideas.",
        "Confident hand-drawn marker contours with rounded starts and stops, mild pressure variation, simple blocky props, and sparse hand-drawn emphasis marks.",
        "Neutral bright white board surface with no room context.",
        "Charcoal black plus one vivid cyan or coral highlighter accent.",
        "tiny details, thin technical lines, airbrushed shading, stock icons, dense decoration, fake writing",
        render=_render(
            block_fill_style="clean",
            stroke_detail="balanced",
            line_thickness=2,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
    ),
    _style(
        "rough-diagram",
        6,
        "手绘草图图解",
        "Rough hand-drawn diagram",
        "白板图解",
        "双描边轻微错位的草图形状、弯箭头和开放式布局，接近流行手绘画布语言。",
        ("架构", "产品", "流程", "脑图"),
        ("rough", "sketch-diagram", "手绘图解"),
        "native",
        "Organize concepts as a few spatial objects connected only by necessary arrows.",
        "Friendly rough-diagram language: slightly bowed rectangles and circles, one or two imperfect contour passes, loose arrows, and simple hand-sketched people.",
        "Warm near-white canvas with no grid and no application chrome.",
        "Charcoal outlines, pale blue fills, one coral accent, and optional muted yellow highlights.",
        "perfect vector geometry, UI screenshots, typed labels, excessive connectors, drop shadows, photorealism",
        render=_render(
            block_fill_style="clean",
            stroke_detail="rich",
            line_thickness=1,
            max_draw_blocks=7,
            draw_blocks=5,
        ),
        provenance="media-language study informed by MIT-licensed Excalidraw and Rough.js; no code or assets embedded",
    ),
    _style(
        "pressure-ink-notes",
        7,
        "压感墨线笔记",
        "Pressure-sensitive ink notes",
        "白板图解",
        "随速度与转向变化的压感线条，兼顾手写亲和力和结构清晰度。",
        ("笔记", "流程", "人物解释", "观点"),
        ("freehand-notes", "pressure-line", "压感线条"),
        "native",
        "Use economical freehand contours whose weight reinforces hierarchy and motion.",
        "Smooth but unmistakably hand-drawn ink contours with natural pressure taper, rounded caps, slightly thicker key silhouettes, and lighter internal detail lines.",
        "Clean soft-white paper with almost invisible fiber.",
        "Near-black ink, warm gray secondary lines, and one restrained cobalt accent.",
        "uniform vector strokes, calligraphy flourishes, heavy shading, dense backgrounds, typed text, glossy color",
        render=_render(
            block_fill_style="clean",
            stroke_detail="rich",
            line_thickness=0,
            block_overlap=0.20,
        ),
        provenance="media-language study informed by MIT-licensed perfect-freehand; no code or assets embedded",
    ),
    _style(
        "semantic-ink",
        8,
        "语义钢笔线稿",
        "Semantic ink drawing",
        "线稿叙事",
        "主轮廓深、内部细节浅的钢笔线稿，人物与道具信息保真度高。",
        ("人物故事", "知识叙事", "纪实", "复杂道具"),
        ("ink-line", "informative-ink", "钢笔线稿"),
        "native",
        "Preserve identity, pose, and object semantics while simplifying surface texture.",
        "Expressive pen drawing with strong complete outer contours, lighter gray internal construction and detail lines, restrained cross-lines, and readable silhouettes.",
        "Warm white sketch paper with very subtle fiber and broad empty margins.",
        "Black, charcoal, warm gray, and one muted rust or blue accent.",
        "photo-edge noise, uniform binary contour, full-page hatching, glossy paint, clutter, tiny texture",
        render=_render(
            block_fill_style="clean",
            stroke_detail="max",
            line_thickness=0,
            line_art_snap_threshold=238,
        ),
    ),
    _style(
        "anime-graphite",
        9,
        "动漫石墨线稿",
        "Anime graphite sketch",
        "线稿叙事",
        "保留灰阶笔压的二维人物石墨线稿，适合插画型角色分镜。",
        ("角色故事", "青春", "二次元人物", "动作"),
        ("anime-pencil", "graphite-anime", "动漫铅笔"),
        "native",
        "Keep recurring character clothing, hair silhouette, and facial proportions exact across scenes. Stage every shot as one continuous cinematic frame with a pale simplified environment behind darker foreground subjects.",
        "Clean animation-key graphite sketch with varied gray pencil pressure, confident outer contours, delicate facial lines, sparse construction marks, and no inked comic finish.",
        "Cool-white animation paper with no desk, holes, frame, or registration marks.",
        "Graphite gray with low-saturation local color appearing only in the final colored layer.",
        "dense manga screentone, black silhouettes, photoreal pencil portrait, messy background, glossy anime rendering, floating scenery cards, hard-edged background rectangles, equal-contrast dense scenery",
        render=_render(
            block_fill_style="soft-wash",
            color_fill_scope="scene",
            stroke_detail="max",
            line_thickness=0,
            line_art_snap_threshold=240,
        ),
    ),
    _style(
        "kid-crayon",
        10,
        "儿童蜡笔坏画",
        "Childlike crayon drawing",
        "儿童涂画",
        "歪比例、重复轮廓、漏白与轻微越界，保留真实儿童画的天真感。",
        ("童年", "亲子", "轻喜剧", "启蒙"),
        ("child-crayon", "bad-crayon", "儿童蜡笔"),
        "adaptive",
        "Keep the subject recognizable even though proportions and mark-making are charmingly clumsy.",
        "Authentic young-child crayon drawing with wobbly repeated outlines, imperfect closure, uneven faces, simple bent limbs, random stroke direction, visible gaps, and occasional coloring outside the lines.",
        "Bright white drawing sheet, evenly lit and isolated from any tabletop.",
        "Basic red, yellow, blue, green, orange, and pink crayons with uneven pressure and large white gaps.",
        "polished children's-book art, commercial cuteness, symmetry, smooth fill, gradients, correct perspective",
        render=_render(
            block_fill_style="crayon",
            stroke_detail="rich",
            line_thickness=0,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "raw-kid-crayon",
        11,
        "潦草家庭蜡笔",
        "Raw family crayon card",
        "儿童涂画",
        "成人歪线稿加孩子乱涂色，笔触更散、更乱、更露白。",
        ("家庭", "温暖日常", "投稿故事", "亲子"),
        ("rawkid", "rawkid-crayon", "family-crayon", "潦草蜡笔"),
        "adaptive",
        "Keep true age and height differences while allowing every figure a different imperfect shape.",
        "Ordinary hand-drawn black outline covered by discontinuous child crayon marks with sudden direction and pressure changes, crossings, gaps, stops, and rare spillover.",
        "Bright untextured white page with only zero to two necessary background props.",
        "Cheerful ordinary crayon colors, never fluorescent; highly uneven coverage with much exposed white.",
        "chibi templates, neat diagonal hatching, uniform coverage, photographed paper, complete room scenes, digital filter",
        render=_render(
            block_fill_style="crayon",
            stroke_detail="rich",
            line_thickness=0,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "bean-doodle-infographic",
        12,
        "小豆人涂鸦信息图",
        "Bean doodle infographic",
        "符号涂鸦",
        "黑色豆形主角、白点眼与单一橙色重点，特别适合步骤与清单。",
        ("步骤", "清单", "知识卡", "轻剧情"),
        ("bean", "blob-doodle", "豆人"),
        "native",
        "Let one recurring bean-shaped character demonstrate each beat through a clear action.",
        "Black marker doodle infographic starring one solid rounded bean character with two white dot eyes, a tiny curved mouth, thin limbs, and simple outlined props.",
        "Pure white page with generous open space.",
        "Black and white with exactly one orange focal object or accent.",
        "realistic people, multiple accent colors, gray shading, textures, detailed scenery, polished vector icons",
        render=_render(
            block_fill_style="clean",
            stroke_detail="balanced",
            line_thickness=1,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "organic-contour-doodle",
        13,
        "有机轮廓涂鸦",
        "Organic contour doodle",
        "品牌涂鸦",
        "自由单线轮廓配一个大胆有机色块，成熟、轻盈且物体边界清楚。",
        ("品牌故事", "生活方式", "餐饮", "轻科普"),
        ("organic-doodle", "contour-doodle", "有机轮廓"),
        "native",
        "Build a rhythmic composition from a few complete objects with strong negative space.",
        "Mature freehand contour illustration with flowing black lines that occasionally overlap, break, or wander beyond forms, paired with a few rounded organic color shapes.",
        "Warm white paper with no decorative pattern.",
        "Black line, one bold main color, and at most one quiet supporting color.",
        "childish crayon, corporate vector people, sticker packs, strict geometry, realistic shading, multicolor clutter",
        render=_render(
            block_fill_style="clean",
            stroke_detail="rich",
            line_thickness=1,
            max_draw_blocks=6,
            draw_blocks=4,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "naive-marker-notes",
        14,
        "稚拙马克笔笔记",
        "Naive marker notes",
        "手写笔记",
        "歪边框、粗符号、箭头与一两种荧光重点，像有想法的人随手记下。",
        ("社媒观点", "复盘", "方法论", "年轻内容"),
        ("marker-notes", "doodle-notes", "马克笔笔记"),
        "native",
        "Keep one dominant illustration and only a few nonverbal marks that clarify hierarchy.",
        "Controlled naive marker-note illustration with slightly crooked contours, symbolic faces, loose boxes, stars, arrows, underlines, and intentional scale jumps while remaining visually ordered.",
        "Clean white notebook-like paper without ruled lines or binder details.",
        "Heavy black marker with one or two fluorescent highlighter colors used sparingly.",
        "typeset words, pseudo-writing, strict grid, polished icons, stock characters, sticker overload, gradients",
        render=_render(
            block_fill_style="clean",
            stroke_detail="rich",
            line_thickness=2,
            max_draw_blocks=7,
            draw_blocks=5,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "notebook-pencil-doodle",
        15,
        "铅笔课堂随记",
        "Notebook pencil doodle",
        "手写笔记",
        "轻石墨线、擦除痕与小型示意图，适合学习笔记和温和解释。",
        ("学习", "课程", "复习", "知识点"),
        ("pencil-notes", "study-doodle", "课堂随记"),
        "native",
        "Arrange the concept as a central sketch with two or three small supporting visual cues.",
        "Loose classroom pencil doodle with soft graphite pressure variation, a few erased construction ghosts, simple diagram arrows, and clean complete object silhouettes.",
        "Pale warm notebook paper without visible writing, ruling, holes, or page edges.",
        "Graphite gray, pale blue pencil, and a tiny muted yellow highlight.",
        "dense graphite shading, realistic still life, typed notes, fake equations, dirty paper, full-page texture",
        render=_render(
            block_fill_style="soft-wash",
            stroke_detail="max",
            line_thickness=0,
            line_art_snap_threshold=240,
        ),
    ),
    _style(
        "ballpoint-scribble",
        16,
        "圆珠笔缠绕线速写",
        "Ballpoint scribble sketch",
        "艺术速写",
        "自由缠绕与回笔形成体积，保留犹豫线和现场手稿呼吸感。",
        ("肖像", "动物", "独白", "情绪"),
        ("scribble", "ballpoint", "圆珠笔速写"),
        "experimental",
        "Use line density to clarify volume without losing the complete outer silhouette.",
        "Fast black ballpoint sketch made from freely looping, overlapping, revisiting lines with open contours, hesitation marks, and sparse line-density modeling.",
        "Plain white sketchbook paper with almost no background cues.",
        "Single black ballpoint ink; no flat color areas.",
        "closed vector contours, uniform crosshatching, comic inking, charcoal smudge, photoreal pencil, dense background",
        render=_render(
            block_fill_style="clean",
            stroke_detail="max",
            line_thickness=0,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "inked-storybook",
        17,
        "墨线淡彩绘本",
        "Inked light-color storybook",
        "绘本线稿",
        "自信松散墨线覆盖透明淡彩，兼顾角色表演和可抽取线条。",
        ("角色故事", "青春", "对白", "轻冒险"),
        ("ink-storybook", "light-color-ink", "墨线绘本"),
        "native",
        "Favor expressive acting and confident silhouettes with only sparse local hatching.",
        "Expressive storybook illustration with confident loose ink lines, slight line-weight variation, a few overlapping construction lines, and transparent local-color washes.",
        "Flat warm-cream paper with one low-detail brush area and broad clean margins.",
        "Two to four coordinated transparent colors; line color may shift darker within each local hue.",
        "full-block hatching, chaotic hair scribbles, thick oil paint, photorealism, full-bleed scenery, vector polish",
        render=_render(
            block_fill_style="soft-wash", stroke_detail="max", line_thickness=0
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "emotional-watercolor-sketch",
        18,
        "情绪淡彩速写",
        "Emotional watercolor sketch",
        "淡彩叙事",
        "靛蓝松散速写线、大留白与一处暖橙焦点，克制而有情绪。",
        ("回忆", "关系", "纪实", "情感"),
        ("light-watercolor", "emo-sketch", "淡彩速写"),
        "adaptive",
        "Frame the beat like a quiet memory fragment with one emotional focal object.",
        "Loose indigo pencil-and-ink sketch with overlapping construction lines, broken contours, transparent watercolor edges, and one gentle warm focal wash.",
        "Bright lightly textured watercolor paper, mostly left untouched.",
        "Indigo, gray-blue, pale skin tones, paper white, and exactly one soft warm-orange focus.",
        "full-page wash, saturated rainbow, hard cartoon outline, commercial cuteness, photoreal light, busy interiors",
        render=_render(
            block_fill_style="soft-wash",
            stroke_detail="max",
            line_thickness=0,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "ink-wash-minimal",
        19,
        "水墨留白",
        "Minimal ink wash",
        "传统手绘",
        "枯湿浓淡墨线、大量宣纸留白与一处低饱和点色，适合寓言文化题材。",
        ("寓言", "传统文化", "历史", "感悟"),
        ("ink-wash", "shuimo", "水墨"),
        "adaptive",
        "Use very few brush marks to capture gesture, identity, and cause-and-effect.",
        "Expressive Chinese ink drawing with dry-brush flying white, wet-to-dry variation, broken strokes, restrained diffusion, and a complete recognizable main silhouette.",
        "Bright warm xuan-paper field with generous active emptiness and no decorative calligraphy.",
        "Black ink in several concentrations, at most a little pale ochre or blue-green, and one tiny abstract vermilion accent without readable glyphs.",
        "uniform grayscale filter, rigid closed contour, even fill, glossy gradient, dense scenery, fake calligraphy, ornate seal text",
        render=_render(
            block_fill_style="dry-brush",
            stroke_detail="max",
            line_thickness=0,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
    ),
    _style(
        "retro-gouache-concept",
        20,
        "中古动画水粉概念稿",
        "Mid-century gouache concept",
        "水粉绘本",
        "奶油纸、铅笔起稿线和哑光水粉大形，具有复古动画概念设计气质。",
        ("怀旧", "城市", "温暖剧情", "角色短片"),
        ("retro-gouache", "mid-century", "中古水粉"),
        "adaptive",
        "Use simplified stage-like scenery, one focal action, and expressive large shapes.",
        "Hand-painted mid-century animation concept sketch with loose pencil underdrawing, matte opaque gouache masses, dry-brush edges, and simplified theatrical depth.",
        "Warm cream paper visible around the composition.",
        "Controlled orange-blue complements with cream yellow, brick red, and dusty blue.",
        "modern anime faces, glossy digital paint, 3D, photorealism, neon, hyper-detail, smooth airbrush, vector geometry",
        render=_render(
            block_fill_style="dry-brush",
            stroke_detail="rich",
            line_thickness=0,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "nordic-gouache-storybook",
        21,
        "北欧低饱和水粉绘本",
        "Nordic gouache storybook",
        "水粉绘本",
        "圆钝人物、低饱和限定色和干刷水粉边缘，安静且留白充足。",
        ("睡前故事", "自然", "安静日常", "生活方式"),
        ("nordic-storybook", "scandi-gouache", "北欧绘本"),
        "adaptive",
        "Keep scenes calm, planar, and sparse, with natural poses and only essential furniture or plants.",
        "Nordic children's gouache with simplified rounded figures, tiny dot eyes, matte dry-brush color edges, and restrained planar props.",
        "Warm white fine-tooth paper with broad quiet negative space.",
        "Denim blue, mustard yellow, brick orange, sage green, and warm gray.",
        "saturated palette, glossy gradients, complex perspective, anime eyes, thick black outlines, 3D, corporate vector art",
        render=_render(
            block_fill_style="dry-brush",
            stroke_detail="rich",
            line_thickness=0,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "sunlit-storybook",
        22,
        "暖光童画绘本",
        "Sunlit storybook vis-dev",
        "水粉绘本",
        "柔软水粉形、暖边光与未完成概念稿留白，适合治愈和童话。",
        ("童话", "治愈", "亲情", "冒险"),
        ("sunlit", "warm-storybook", "暖光童画"),
        "adaptive",
        "Build characters from soft large shapes and keep the background as only a few loose color cues.",
        "Warm visual-development storybook painting with soft matte gouache, dry scumbles, fluffy shape language, clear expressions, and a deliberately unfinished paper-edge quality.",
        "Light warm paper remaining visibly open around the scene.",
        "Powder blue, cream yellow, soft orange, and a little blue-green with restrained warm rim light.",
        "sharp anime hair, plastic highlights, 3D, realistic faces, full-bleed scenery, vector edges, excessive polish",
        render=_render(
            block_fill_style="soft-wash",
            stroke_detail="rich",
            line_thickness=0,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "warm-flat-storybook",
        23,
        "暖色几何扁平绘本",
        "Warm flat storybook",
        "扁平绘本",
        "圆润几何色形、极细局部线和严格限定色板，适合品牌与轻科普。",
        ("品牌", "关系", "轻科普", "现代寓言"),
        ("flat-storybook", "warm-flat", "扁平绘本"),
        "experimental",
        "Use a few large rounded shapes and keep roughly two thirds of the canvas open.",
        "Contemporary flat storybook made from large rounded geometric color shapes, near-invisible outer contour, and only tiny navy detail lines for faces and fingers.",
        "Warm white paper field with no gradients or scene frame.",
        "Strict navy, mist blue, coral orange, golden orange, warm peach, and warm white; one hard same-hue shadow shape at most.",
        "corporate vector people, chibi faces, thick outlines, 3D, glossy gradients, complex scenery, painterly texture, extra hues",
        render=_render(
            block_fill_style="clean",
            stroke_detail="balanced",
            line_thickness=0,
            line_art_snap_threshold=242,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "zine-riso-collage",
        24,
        "Zine 孔版拼贴",
        "Zine riso collage",
        "版画拼贴",
        "手撕边、复印颗粒与双色套印偏移形成 DIY 小志质感。",
        ("旅行", "音乐", "青年文化", "成长"),
        ("zine", "riso", "孔版拼贴"),
        "experimental",
        "Use only a few torn or cut elements that directly serve the current beat.",
        "DIY zine collage combining simplified cut-paper subjects, sparse hand-ink contours, a little tape, photocopy grain, halftone, and visibly imperfect two-color registration.",
        "Warm white paper with clean outer margins and no photographed desk.",
        "Black copy ink plus two riso-like spot colors chosen for strong separation.",
        "polished scrapbook, real logos, readable newspaper text, sticker overload, gradients, 3D shadows, photoreal environments",
        render=_render(
            block_fill_style="clean",
            stroke_detail="rich",
            line_thickness=1,
            max_draw_blocks=7,
            draw_blocks=5,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "manga-screentone",
        25,
        "黑白漫画网点",
        "Manga screentone",
        "漫画线稿",
        "清晰动作墨线、有限灰阶网点和高反差节奏，适合冲突与反转。",
        ("反转", "冲突", "动作", "悬念"),
        ("manga", "screentone", "漫画网点"),
        "experimental",
        "Prioritize pose, expression, and one strong focal action; keep tones large and sparse.",
        "Original black-and-white comic drawing with crisp hand-ink contours, selective speed lines, and only two coarse program-like screentone densities that remain visually separable.",
        "Pure white page without panels, gutters, speech bubbles, or trim marks.",
        "One-bit black and white plus two stable gray tone values; no colored fill.",
        "copied comic imagery, recognizable franchise design, dense moire patterns, speech balloons, tiny halftone, full-page black",
        render=_render(
            block_fill_style="clean",
            stroke_detail="max",
            line_thickness=1,
            line_art_snap_threshold=228,
            draw_blocks=3,
        ),
    ),
    _style(
        "linocut-editorial",
        26,
        "粗粝木刻社论",
        "Rough linocut editorial",
        "版画线稿",
        "粗黑形块、刀刻留白和一块暗红或深蓝套色，视觉力量最强。",
        ("历史", "社会议题", "寓言", "社论"),
        ("linocut", "woodcut", "木刻"),
        "experimental",
        "Reduce the beat to one bold symbol and retain enough open white cuts to expose structure.",
        "Hand-cut linocut editorial illustration with irregular heavy black masses, carved white channels, rough chipped contours, directional but nonmechanical cuts, and slight registration imperfection.",
        "Warm white fibrous paper kept light enough for line extraction.",
        "Black ink plus one dark brick-red or deep-blue spot color.",
        "photorealism, delicate pencil, smooth vector edges, uniform noise, rainbow colors, gradients, ornate lettering, excessively fine hatching",
        render=_render(
            block_fill_style="dry-brush",
            stroke_detail="rich",
            line_thickness=1,
            line_art_snap_threshold=215,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "blueprint-pencil",
        27,
        "浅底蓝图铅笔",
        "Light-paper blueprint pencil",
        "工程草图",
        "藏蓝结构线、浅辅助线与橙色重点，在浅底上保留蓝图秩序又适合抽线。",
        ("建筑", "机械", "产品", "空间关系"),
        ("blueprint", "engineering-sketch", "工程蓝图"),
        "native",
        "Use precise large geometry, a few construction guides, and clear part-to-part relationships without generated labels.",
        "Hand-drafted engineering sketch on light paper with confident navy contours, pale blue construction lines, occasional dashed hidden edges, and clean simplified people for scale.",
        "Very pale blue-white engineering paper without grid numbers, title block, frame, or dimensions.",
        "Navy and pale cyan linework with exactly one orange functional emphasis.",
        "dark blueprint background, machine-generated text, dimensions, perfect CAD output, photoreal product rendering, dense crosshatching",
        render=_render(
            block_fill_style="clean",
            stroke_detail="max",
            line_thickness=0,
            max_draw_blocks=7,
            draw_blocks=5,
        ),
    ),
    _style(
        "editorial-portrait",
        28,
        "编辑肖像线描",
        "Editorial portrait linework",
        "人物线描",
        "五官重点精细、衣发概括、背景淡化，适合人物传记和访谈故事。",
        ("人物传记", "访谈", "名人故事", "观点"),
        ("portrait-line", "editorial-line", "编辑肖像"),
        "adaptive",
        "Protect facial identity and expression while simplifying clothing and all background information.",
        "Editorial portrait drawing with accurate expressive face, varied charcoal-ink contour, selectively modeled eyes and mouth, simplified hair and clothing, and a few loose gestural background marks.",
        "Warm gray-white paper with generous portrait breathing room.",
        "Black and warm gray with one restrained skin, rust, or brand-color accent.",
        "photo-edge tracing, uncanny realism, beauty retouching, dense room, glossy painting, tiny decorative texture",
        render=_render(
            block_fill_style="soft-wash",
            stroke_detail="max",
            line_thickness=0,
            max_draw_blocks=4,
            draw_blocks=2,
        ),
    ),
    _style(
        "ms-paint-doodle",
        29,
        "鼠标锯齿涂鸦",
        "Mouse-drawn pixel doodle",
        "故意画烂",
        "硬锯齿歪线、荒谬比例和少量纯色色块，适合吐槽与病毒感反转。",
        ("吐槽", "荒诞", "反转", "轻喜剧"),
        ("ms-paint", "ms-paint-bad-doodle", "bad-doodle", "鼠标涂鸦"),
        "experimental",
        "Make the action instantly understandable despite intentionally awkward anatomy and geometry.",
        "Crude mouse-drawn doodle with jagged pixel stair-steps, shaky discontinuous lines, absurd proportions, hard-edged flat fills, and obvious amateur mistakes.",
        "Featureless bright white digital canvas.",
        "A few recognizable high-contrast solid colors with gaps and spillover.",
        "antialiasing, polished illustration, smooth curves, correct anatomy, gradients, realistic light, texture, professional composition",
        render=_render(
            block_fill_style="clean",
            stroke_detail="balanced",
            line_thickness=1,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
    _style(
        "real-crayon-paper",
        30,
        "真实蜡笔纸感",
        "Real crayon on paper",
        "儿童涂画",
        "能看到纸齿、蜡质结块、压力变化和大量漏白，但保持浅底与稳定构图。",
        ("成长记录", "儿童视角", "亲子", "童年"),
        ("paper-crayon", "physical-crayon", "真实蜡笔"),
        "adaptive",
        "Preserve the full sheet-like composition while keeping every story subject large and recognizable.",
        "Authentic physical crayon drawing with wobbly contours, visible paper-tooth skips, wax buildup under heavy pressure, broken light strokes, uneven direction, and several honest spillovers.",
        "Bright white paper fills the canvas under flat soft daylight; no desk, fold, mockup, shadow, or page frame.",
        "Bright basic crayons with highly uneven pressure and roughly half the color regions showing white gaps.",
        "digital illustration, uniform crayon texture, smooth fill, polished anatomy, dark tabletop, moody shadow, commercial cuteness",
        render=_render(
            block_fill_style="crayon",
            stroke_detail="rich",
            line_thickness=0,
            max_draw_blocks=5,
            draw_blocks=3,
        ),
        provenance="adapted from story-to-handdrawn-video and its MIT-attributed style library",
    ),
)


def available_styles() -> tuple[VisualStyle, ...]:
    """Return built-in recipes in stable display order."""

    return BUILTIN_STYLES


def _selector_key(value: str) -> str:
    return re.sub(r"[\s_]+", "-", value.strip().lower()).strip("-")


def _style_index() -> dict[str, VisualStyle]:
    index: dict[str, VisualStyle] = {}
    for style in BUILTIN_STYLES:
        selectors = (
            style.id,
            str(style.order),
            style.name_zh,
            style.name_en,
            *style.aliases,
        )
        for selector in selectors:
            key = _selector_key(selector)
            existing = index.get(key)
            if existing is not None and existing.id != style.id:
                raise RuntimeError(f"Duplicate visual-style selector: {selector}")
            index[key] = style
    return index


def resolve_builtin_style(selector: str | None = None) -> VisualStyle:
    """Resolve an id, order, localized name, or alias."""

    value = selector or DEFAULT_STYLE_ID
    style = _style_index().get(_selector_key(value))
    if style is None:
        choices = ", ".join(item.id for item in BUILTIN_STYLES)
        raise ValueError(f"Unknown visual style '{value}'. Available styles: {choices}")
    return style


_AUTO_STYLE_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "blueprint-pencil",
        (
            "建筑",
            "机械",
            "工程",
            "产品结构",
            "architecture",
            "engineering",
            "mechanical",
        ),
    ),
    (
        "clean-whiteboard",
        ("流程", "步骤", "方法", "商业", "教程", "process", "tutorial", "business"),
    ),
    (
        "ink-wash-minimal",
        ("古代", "寓言", "和尚", "诗词", "传统", "禅", "ancient", "fable", "zen"),
    ),
    ("kid-crayon", ("孩子", "儿童", "童年", "幼儿", "child", "kid", "childhood")),
    (
        "emotional-watercolor-sketch",
        ("回忆", "离别", "思念", "关系", "眼泪", "memory", "farewell", "grief"),
    ),
    (
        "editorial-portrait",
        ("传记", "人物", "访谈", "一生", "biography", "interview", "portrait"),
    ),
    (
        "linocut-editorial",
        ("历史", "战争", "社会", "冲突", "history", "war", "society"),
    ),
    ("manga-screentone", ("反转", "决斗", "追逐", "悬念", "twist", "battle", "chase")),
    ("zine-riso-collage", ("旅行", "音乐", "乐队", "青春", "travel", "music", "band")),
    (
        "bean-doodle-infographic",
        ("清单", "三个要点", "五件事", "list", "tips", "checklist"),
    ),
)


def recommend_styles(script: str, limit: int = 5) -> tuple[VisualStyle, ...]:
    """Return deterministic content-aware suggestions without a model call."""

    if limit < 1:
        raise ValueError("style recommendation limit must be positive")
    haystack = script.casefold()
    scored: list[tuple[int, int, VisualStyle]] = []
    for style_id, keywords in _AUTO_STYLE_RULES:
        score = sum(haystack.count(keyword.casefold()) for keyword in keywords)
        if score:
            style = resolve_builtin_style(style_id)
            scored.append((-score, style.order, style))
    fallback_ids = (
        DEFAULT_STYLE_ID,
        "clean-whiteboard",
        "colored-pencil-diary",
        "inked-storybook",
        "organic-contour-doodle",
    )
    seen: set[str] = set()
    ordered: list[VisualStyle] = []
    for _negative_score, _order, style in sorted(scored):
        if style.id not in seen:
            seen.add(style.id)
            ordered.append(style)
    for style_id in fallback_ids:
        style = resolve_builtin_style(style_id)
        if style.id not in seen:
            seen.add(style.id)
            ordered.append(style)
    compatibility_rank = {"native": 0, "adaptive": 1, "experimental": 2}
    for style in sorted(
        BUILTIN_STYLES,
        key=lambda item: (compatibility_rank[item.compatibility], item.order),
    ):
        if style.id not in seen:
            seen.add(style.id)
            ordered.append(style)
    return tuple(ordered[:limit])


def load_custom_style(path: Path) -> VisualStyle:
    """Load a constrained JSON recipe or a plain UTF-8 visual description."""

    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(f"Custom style file does not exist: {path}")
    if path.stat().st_size > MAX_CUSTOM_STYLE_BYTES:
        raise ValueError("Custom style file must be at most 64 KiB")
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        raise ValueError("Custom style file cannot be empty")
    if text.startswith("{"):
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid custom style JSON in {path}: {exc.msg}") from exc
        if not isinstance(payload, dict):
            raise TypeError("Custom style JSON must be an object")
        return _style_from_overlay(payload)
    return custom_style_from_text(text, name=path.stem)


def custom_style_from_text(
    description: str, *, name: str = "Custom style"
) -> VisualStyle:
    """Create a safe custom recipe from one bounded natural-language description."""

    cleaned = " ".join(description.split())
    if not cleaned:
        raise ValueError("Custom style description cannot be empty")
    if len(cleaned) > 4_000:
        raise ValueError("Custom style description must be at most 4000 characters")
    digest = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()[:12]
    return VisualStyle(
        id=f"custom-{digest}",
        name_zh=name,
        name_en=name,
        family="自定义",
        summary=cleaned[:240],
        best_for=("custom",),
        compatibility="adaptive",
        planner_guidance="Follow the custom visual direction while preserving simple complete silhouettes and separable objects.",
        aesthetic=cleaned,
        paper="Use a light, uncluttered drawing surface compatible with dark line extraction unless the description is more specific.",
        palette="Follow the custom direction with a restrained coherent palette and clearly separated regions.",
        avoid="conflicting visual media, illegible detail, dense texture, generated writing, logos, and cropped subjects",
        render=RenderStyle(),
        provenance="user-authored custom style",
    )


_CUSTOM_KEYS = {
    "schema_version",
    "id",
    "order",
    "name_zh",
    "name_en",
    "family",
    "summary",
    "best_for",
    "aliases",
    "compatibility",
    "planner_guidance",
    "aesthetic",
    "paper",
    "palette",
    "avoid",
    "render",
    "extends",
}


def _style_from_overlay(payload: dict[str, object]) -> VisualStyle:
    unexpected = set(payload).difference(_CUSTOM_KEYS)
    if unexpected:
        raise ValueError(
            "Unsupported custom style fields: " + ", ".join(sorted(unexpected))
        )
    if "schema_version" in payload and type(payload["schema_version"]) is not int:
        raise ValueError("Custom style schema_version must be the integer 1")
    extends = payload.get("extends")
    if extends is not None and not isinstance(extends, str):
        raise TypeError("Custom style extends must be a built-in style selector")
    explicit_id = payload.get("id")
    if explicit_id is not None and (
        not isinstance(explicit_id, str) or not explicit_id.startswith("custom-")
    ):
        raise ValueError("Custom style id must start with 'custom-'")
    base = resolve_builtin_style(extends or DEFAULT_STYLE_ID).model_dump(mode="python")
    base_render = dict(base["render"])
    raw_render = payload.get("render", {})
    if not isinstance(raw_render, dict):
        raise TypeError("Custom style render must be an object")
    base_render.update(raw_render)
    normalized_payload = dict(payload)
    for field_name in ("best_for", "aliases"):
        field_value = normalized_payload.get(field_name)
        if isinstance(field_value, list):
            # JSON arrays are the canonical representation of immutable tuple fields.
            # Normalize only the container; strict model validation below still
            # rejects non-string members and every other coercion.
            normalized_payload[field_name] = tuple(field_value)
    merged = {
        **base,
        **{
            key: value
            for key, value in normalized_payload.items()
            if key not in {"extends", "render"}
        },
        "render": base_render,
        "order": normalized_payload.get("order", 0),
        "provenance": "user-authored custom style file",
    }
    if "id" not in payload:
        semantic = json.dumps(merged, ensure_ascii=False, sort_keys=True, default=str)
        merged["id"] = (
            f"custom-{hashlib.sha256(semantic.encode('utf-8')).hexdigest()[:12]}"
        )
    return VisualStyle.model_validate(merged, strict=True)


def resolve_style(
    selector: str | None = None,
    *,
    script: str = "",
    custom_style: str | None = None,
    custom_style_file: Path | None = None,
) -> VisualStyle:
    """Resolve one mutually exclusive built-in, automatic, inline, or file style."""

    selected = sum(
        value is not None for value in (selector, custom_style, custom_style_file)
    )
    if selected > 1:
        raise ValueError("Use only one of style, custom_style, or custom_style_file")
    if custom_style_file is not None:
        return load_custom_style(custom_style_file)
    if custom_style is not None:
        return custom_style_from_text(custom_style)
    if selector and _selector_key(selector) == "auto":
        return recommend_styles(script, limit=1)[0]
    return resolve_builtin_style(selector)


def build_storyboard_prompt(
    content_prompt: str,
    style: VisualStyle,
    *,
    theme: str | None = None,
) -> str:
    """Build an idempotent effective prompt with non-overridable production rules."""

    base = content_prompt.split(STYLE_PROMPT_MARKER, 1)[0].strip()
    if LEGACY_STYLE_SUFFIX in base:
        base = base.replace(LEGACY_STYLE_SUFFIX, "").rstrip(" ,")
    cleaned_theme = " ".join((theme or "").split())
    if len(cleaned_theme) > 1_000:
        raise ValueError("Theme direction must be at most 1000 characters")
    sections = [
        base,
        STYLE_PROMPT_MARKER.strip(),
        f"Style recipe: {style.aesthetic}",
        f"Paper/background: {style.paper}",
        f"Palette and fill: {style.palette}",
    ]
    if style.render.color_fill_scope == "scene":
        sections.append(
            f"Background layer contract: {_WHOLE_SCENE_BACKGROUND_CONTRACT}"
        )
    if cleaned_theme:
        sections.append(f"Story theme and art direction: {cleaned_theme}")
    sections.extend(
        (
            f"Avoid: {style.avoid}",
            f"Production contract: {_PRODUCTION_CONTRACT}",
        )
    )
    return "\n".join(section for section in sections if section)


def style_semantic_payload(style: VisualStyle) -> dict[str, object]:
    """Return only output-affecting fields for cache fingerprints."""

    return {
        "storyboard_prompt_schema": STORYBOARD_PROMPT_SCHEMA,
        "schema_version": style.schema_version,
        "id": style.id,
        "planner_guidance": style.planner_guidance,
        "aesthetic": style.aesthetic,
        "paper": style.paper,
        "palette": style.palette,
        "avoid": style.avoid,
        "render": style.render.model_dump(mode="json"),
    }


def style_planning_payload(style: VisualStyle) -> dict[str, object]:
    """Return only fields that can change scene planning or storyboard pixels."""

    return {
        "storyboard_prompt_schema": STORYBOARD_PROMPT_SCHEMA,
        "schema_version": style.schema_version,
        "planner_guidance": style.planner_guidance,
        "aesthetic": style.aesthetic,
        "paper": style.paper,
        "palette": style.palette,
        "avoid": style.avoid,
        "background_contract": (
            _WHOLE_SCENE_BACKGROUND_CONTRACT
            if style.render.color_fill_scope == "scene"
            else ""
        ),
        "production_contract": _PRODUCTION_CONTRACT,
    }


def style_display_payload(style: VisualStyle) -> dict[str, object]:
    """Return a stable, machine-readable public recipe for CLI discovery."""

    return {
        "schema_version": style.schema_version,
        "order": style.order,
        "id": style.id,
        "name_zh": style.name_zh,
        "name_en": style.name_en,
        "family": style.family,
        "compatibility": style.compatibility,
        "best_for": list(style.best_for),
        "aliases": list(style.aliases),
        "summary": style.summary,
        "planner_guidance": style.planner_guidance,
        "aesthetic": style.aesthetic,
        "paper": style.paper,
        "palette": style.palette,
        "avoid": style.avoid,
        "render": style.render.model_dump(mode="json"),
        "provenance": style.provenance,
    }
