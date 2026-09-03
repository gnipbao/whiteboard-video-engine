# 视觉风格画廊：卖火柴的小女孩

本画廊用同一幅《卖火柴的小女孩》标准分镜，对比引擎当前注册的 30 种视觉风格。
编号、风格 ID、名称、特征、推荐场景与白板适配等级均以
[`src/whiteboard_skill/styles.py`](../src/whiteboard_skill/styles.py) 的
`BUILTIN_STYLES` 注册顺序为准；图片文件名中的两位序号与 `order` 一一对应。

> 画廊只比较视觉语言。构图、人物、道具和叙事信息保持不变，因此不同图片之间的
> 差异应主要来自画材、线条、色板、纸面与填色方法。

## 统一标准分镜（canonical scene）

- **画幅与时空**：16:9；19 世纪哥本哈根，蓝调时刻的雪夜。
- **主角簇**：小女孩全身倚着简洁墙面，坐在画面左下至中部，屈膝，把一根点燃的
  火柴靠近脸庞。她、手中的火柴、身旁完整的小篮子和成捆未售出的火柴，构成一个
  完整、连续、适合逐笔绘制的视觉簇。
- **幻景簇**：画面右上方保留一个柔和、独立的幻景，其中同时出现老式铁炉和慈爱
  的祖母；它与主角簇自然分离，作为第二个可独立绘制的对象簇。
- **空间与环境**：两个簇之间保留大面积留白；环境只保留稀疏鹅卵石和落雪，不加入
  其他人物或复杂街景。
- **生产约束**：无文字、无边框、无水印；主体不得裁切；线稿与色层应可分离，轮廓
  和主要色区应适合白板逐笔绘制与分块显色。

允许随风格变化的内容仅包括画材质感、线条语言、纸面、色板、局部形状概括和安全的
渲染参数。人物位置、完整道具、双簇关系和叙事含义不得随风格改变。

## 30 风格联系表

每张联系表包含连续六种风格；点击可查看原图。单张风格参考图的精确路径见下方索引。

<p align="center">
  <a href="assets/style-gallery/little-match-girl/contact-sheet-01.png"><img src="assets/style-gallery/little-match-girl/contact-sheet-01.png" alt="风格 01–06 联系表" width="49%"></a>
  <a href="assets/style-gallery/little-match-girl/contact-sheet-02.png"><img src="assets/style-gallery/little-match-girl/contact-sheet-02.png" alt="风格 07–12 联系表" width="49%"></a>
  <a href="assets/style-gallery/little-match-girl/contact-sheet-03.png"><img src="assets/style-gallery/little-match-girl/contact-sheet-03.png" alt="风格 13–18 联系表" width="49%"></a>
  <a href="assets/style-gallery/little-match-girl/contact-sheet-04.png"><img src="assets/style-gallery/little-match-girl/contact-sheet-04.png" alt="风格 19–24 联系表" width="49%"></a>
  <a href="assets/style-gallery/little-match-girl/contact-sheet-05.png"><img src="assets/style-gallery/little-match-girl/contact-sheet-05.png" alt="风格 25–30 联系表" width="49%"></a>
</p>

## 30 风格索引

适配等级说明：`native` 最适合当前抽线、逐笔绘制和对象分块流程；`adaptive` 建议先做
短预览；`experimental` 的密集纹理、黑色形块或弱轮廓可能挑战骨架追踪，需按具体画面
验证。参考图路径均相对于本文件。

| # | 风格（ID / 名称） | 适用类型 | 白板适配 | 特征 | 推荐场景 | 参考图路径 |
| ---: | --- | --- | --- | --- | --- | --- |
| 01 | `warm-crayon-storybook`<br>暖色蜡笔故事书（默认） / Warm crayon storybook | 蜡笔绘本 | `native` | 清晰深色轮廓、温暖蜡笔平涂和充足留白，最适合故事分镜逐层绘制。 | 寓言、儿童故事、生活故事、知识启蒙 | [`01-warm-crayon-storybook.png`](assets/style-gallery/little-match-girl/01-warm-crayon-storybook.png) |
| 02 | `colored-pencil-diary`<br>彩铅日记漫画 / Colored-pencil diary comic | 彩铅叙事 | `native` | 笨拙毡尖轮廓配低饱和彩铅短笔触，像亲手记录的生活日记。 | 家庭、纪实、情感、日常 | [`02-colored-pencil-diary.png`](assets/style-gallery/little-match-girl/02-colored-pencil-diary.png) |
| 03 | `clean-whiteboard`<br>经典清爽白板 / Clean whiteboard | 白板讲解 | `native` | 黑色圆头白板笔和极少彩色重点，结构最清楚、抽线最稳定。 | 教程、商业解释、流程、时间线 | [`03-clean-whiteboard.png`](assets/style-gallery/little-match-girl/03-clean-whiteboard.png) |
| 04 | `minimal-line-explainer`<br>极简黑白线条讲解 / Minimal line explainer | 白板讲解 | `native` | 火柴人与极少道具构成的纯黑白解释图，信息读取速度最快。 | 观点、步骤、科普、概念 | [`04-minimal-line-explainer.png`](assets/style-gallery/little-match-girl/04-minimal-line-explainer.png) |
| 05 | `marker-whiteboard`<br>粗马克笔白板 / Bold marker whiteboard | 白板讲解 | `native` | 粗细略变的白板笔、强动作剪影和局部荧光标记，适合快速短视频。 | 短视频、营销解释、清单、强观点 | [`05-marker-whiteboard.png`](assets/style-gallery/little-match-girl/05-marker-whiteboard.png) |
| 06 | `rough-diagram`<br>手绘草图图解 / Rough hand-drawn diagram | 白板图解 | `native` | 双描边轻微错位的草图形状、弯箭头和开放式布局，接近流行手绘画布语言。 | 架构、产品、流程、脑图 | [`06-rough-diagram.png`](assets/style-gallery/little-match-girl/06-rough-diagram.png) |
| 07 | `pressure-ink-notes`<br>压感墨线笔记 / Pressure-sensitive ink notes | 白板图解 | `native` | 随速度与转向变化的压感线条，兼顾手写亲和力和结构清晰度。 | 笔记、流程、人物解释、观点 | [`07-pressure-ink-notes.png`](assets/style-gallery/little-match-girl/07-pressure-ink-notes.png) |
| 08 | `semantic-ink`<br>语义钢笔线稿 / Semantic ink drawing | 线稿叙事 | `native` | 主轮廓深、内部细节浅的钢笔线稿，人物与道具信息保真度高。 | 人物故事、知识叙事、纪实、复杂道具 | [`08-semantic-ink.png`](assets/style-gallery/little-match-girl/08-semantic-ink.png) |
| 09 | `anime-graphite`<br>动漫石墨线稿 / Anime graphite sketch | 线稿叙事 | `native` | 保留灰阶笔压的二维人物石墨线稿，适合插画型角色分镜。 | 角色故事、青春、二次元人物、动作 | [`09-anime-graphite.png`](assets/style-gallery/little-match-girl/09-anime-graphite.png) |
| 10 | `kid-crayon`<br>儿童蜡笔坏画 / Childlike crayon drawing | 儿童涂画 | `adaptive` | 歪比例、重复轮廓、漏白与轻微越界，保留真实儿童画的天真感。 | 童年、亲子、轻喜剧、启蒙 | [`10-kid-crayon.png`](assets/style-gallery/little-match-girl/10-kid-crayon.png) |
| 11 | `raw-kid-crayon`<br>潦草家庭蜡笔 / Raw family crayon card | 儿童涂画 | `adaptive` | 成人歪线稿加孩子乱涂色，笔触更散、更乱、更露白。 | 家庭、温暖日常、投稿故事、亲子 | [`11-raw-kid-crayon.png`](assets/style-gallery/little-match-girl/11-raw-kid-crayon.png) |
| 12 | `bean-doodle-infographic`<br>小豆人涂鸦信息图 / Bean doodle infographic | 符号涂鸦 | `native` | 黑色豆形主角、白点眼与单一橙色重点，特别适合步骤与清单。 | 步骤、清单、知识卡、轻剧情 | [`12-bean-doodle-infographic.png`](assets/style-gallery/little-match-girl/12-bean-doodle-infographic.png) |
| 13 | `organic-contour-doodle`<br>有机轮廓涂鸦 / Organic contour doodle | 品牌涂鸦 | `native` | 自由单线轮廓配一个大胆有机色块，成熟、轻盈且物体边界清楚。 | 品牌故事、生活方式、餐饮、轻科普 | [`13-organic-contour-doodle.png`](assets/style-gallery/little-match-girl/13-organic-contour-doodle.png) |
| 14 | `naive-marker-notes`<br>稚拙马克笔笔记 / Naive marker notes | 手写笔记 | `native` | 歪边框、粗符号、箭头与一两种荧光重点，像有想法的人随手记下。 | 社媒观点、复盘、方法论、年轻内容 | [`14-naive-marker-notes.png`](assets/style-gallery/little-match-girl/14-naive-marker-notes.png) |
| 15 | `notebook-pencil-doodle`<br>铅笔课堂随记 / Notebook pencil doodle | 手写笔记 | `native` | 轻石墨线、擦除痕与小型示意图，适合学习笔记和温和解释。 | 学习、课程、复习、知识点 | [`15-notebook-pencil-doodle.png`](assets/style-gallery/little-match-girl/15-notebook-pencil-doodle.png) |
| 16 | `ballpoint-scribble`<br>圆珠笔缠绕线速写 / Ballpoint scribble sketch | 艺术速写 | `experimental` | 自由缠绕与回笔形成体积，保留犹豫线和现场手稿呼吸感。 | 肖像、动物、独白、情绪 | [`16-ballpoint-scribble.png`](assets/style-gallery/little-match-girl/16-ballpoint-scribble.png) |
| 17 | `inked-storybook`<br>墨线淡彩绘本 / Inked light-color storybook | 绘本线稿 | `native` | 自信松散墨线覆盖透明淡彩，兼顾角色表演和可抽取线条。 | 角色故事、青春、对白、轻冒险 | [`17-inked-storybook.png`](assets/style-gallery/little-match-girl/17-inked-storybook.png) |
| 18 | `emotional-watercolor-sketch`<br>情绪淡彩速写 / Emotional watercolor sketch | 淡彩叙事 | `adaptive` | 靛蓝松散速写线、大留白与一处暖橙焦点，克制而有情绪。 | 回忆、关系、纪实、情感 | [`18-emotional-watercolor-sketch.png`](assets/style-gallery/little-match-girl/18-emotional-watercolor-sketch.png) |
| 19 | `ink-wash-minimal`<br>水墨留白 / Minimal ink wash | 传统手绘 | `adaptive` | 枯湿浓淡墨线、大量宣纸留白与一处低饱和点色，适合寓言文化题材。 | 寓言、传统文化、历史、感悟 | [`19-ink-wash-minimal.png`](assets/style-gallery/little-match-girl/19-ink-wash-minimal.png) |
| 20 | `retro-gouache-concept`<br>中古动画水粉概念稿 / Mid-century gouache concept | 水粉绘本 | `adaptive` | 奶油纸、铅笔起稿线和哑光水粉大形，具有复古动画概念设计气质。 | 怀旧、城市、温暖剧情、角色短片 | [`20-retro-gouache-concept.png`](assets/style-gallery/little-match-girl/20-retro-gouache-concept.png) |
| 21 | `nordic-gouache-storybook`<br>北欧低饱和水粉绘本 / Nordic gouache storybook | 水粉绘本 | `adaptive` | 圆钝人物、低饱和限定色和干刷水粉边缘，安静且留白充足。 | 睡前故事、自然、安静日常、生活方式 | [`21-nordic-gouache-storybook.png`](assets/style-gallery/little-match-girl/21-nordic-gouache-storybook.png) |
| 22 | `sunlit-storybook`<br>暖光童画绘本 / Sunlit storybook vis-dev | 水粉绘本 | `adaptive` | 柔软水粉形、暖边光与未完成概念稿留白，适合治愈和童话。 | 童话、治愈、亲情、冒险 | [`22-sunlit-storybook.png`](assets/style-gallery/little-match-girl/22-sunlit-storybook.png) |
| 23 | `warm-flat-storybook`<br>暖色几何扁平绘本 / Warm flat storybook | 扁平绘本 | `experimental` | 圆润几何色形、极细局部线和严格限定色板，适合品牌与轻科普。 | 品牌、关系、轻科普、现代寓言 | [`23-warm-flat-storybook.png`](assets/style-gallery/little-match-girl/23-warm-flat-storybook.png) |
| 24 | `zine-riso-collage`<br>Zine 孔版拼贴 / Zine riso collage | 版画拼贴 | `experimental` | 手撕边、复印颗粒与双色套印偏移形成 DIY 小志质感。 | 旅行、音乐、青年文化、成长 | [`24-zine-riso-collage.png`](assets/style-gallery/little-match-girl/24-zine-riso-collage.png) |
| 25 | `manga-screentone`<br>黑白漫画网点 / Manga screentone | 漫画线稿 | `experimental` | 清晰动作墨线、有限灰阶网点和高反差节奏，适合冲突与反转。 | 反转、冲突、动作、悬念 | [`25-manga-screentone.png`](assets/style-gallery/little-match-girl/25-manga-screentone.png) |
| 26 | `linocut-editorial`<br>粗粝木刻社论 / Rough linocut editorial | 版画线稿 | `experimental` | 粗黑形块、刀刻留白和一块暗红或深蓝套色，视觉力量最强。 | 历史、社会议题、寓言、社论 | [`26-linocut-editorial.png`](assets/style-gallery/little-match-girl/26-linocut-editorial.png) |
| 27 | `blueprint-pencil`<br>浅底蓝图铅笔 / Light-paper blueprint pencil | 工程草图 | `native` | 藏蓝结构线、浅辅助线与橙色重点，在浅底上保留蓝图秩序又适合抽线。 | 建筑、机械、产品、空间关系 | [`27-blueprint-pencil.png`](assets/style-gallery/little-match-girl/27-blueprint-pencil.png) |
| 28 | `editorial-portrait`<br>编辑肖像线描 / Editorial portrait linework | 人物线描 | `adaptive` | 五官重点精细、衣发概括、背景淡化，适合人物传记和访谈故事。 | 人物传记、访谈、名人故事、观点 | [`28-editorial-portrait.png`](assets/style-gallery/little-match-girl/28-editorial-portrait.png) |
| 29 | `ms-paint-doodle`<br>鼠标锯齿涂鸦 / Mouse-drawn pixel doodle | 故意画烂 | `experimental` | 硬锯齿歪线、荒谬比例和少量纯色色块，适合吐槽与病毒感反转。 | 吐槽、荒诞、反转、轻喜剧 | [`29-ms-paint-doodle.png`](assets/style-gallery/little-match-girl/29-ms-paint-doodle.png) |
| 30 | `real-crayon-paper`<br>真实蜡笔纸感 / Real crayon on paper | 儿童涂画 | `adaptive` | 能看到纸齿、蜡质结块、压力变化和大量漏白，但保持浅底与稳定构图。 | 成长记录、儿童视角、亲子、童年 | [`30-real-crayon-paper.png`](assets/style-gallery/little-match-girl/30-real-crayon-paper.png) |

## 按内容快速选择

| 内容目标 | 优先尝试 | 选择提示 |
| --- | --- | --- |
| 教程、流程、概念解释 | `clean-whiteboard`、`minimal-line-explainer`、`marker-whiteboard`、`rough-diagram` | 先从 `native` 开始；信息密度高时优先减少装饰和填色。 |
| 人物故事、寓言、儿童叙事 | `warm-crayon-storybook`、`colored-pencil-diary`、`semantic-ink`、`inked-storybook` | 兼顾角色表演与轮廓完整度，适合连续分镜。 |
| 回忆、亲情、安静情绪 | `emotional-watercolor-sketch`、`nordic-gouache-storybook`、`sunlit-storybook` | 属于 `adaptive`，建议先渲染短镜头检查淡彩和柔边。 |
| 文化、历史、社论表达 | `ink-wash-minimal`、`retro-gouache-concept`、`linocut-editorial` | 注意黑色形块和纹理密度；木刻风格需重点检查抽线结果。 |
| 品牌、社媒、年轻化内容 | `organic-contour-doodle`、`naive-marker-notes`、`zine-riso-collage`、`ms-paint-doodle` | 从清晰轮廓方案开始；拼贴和像素涂鸦用于需要强烈个性的场景。 |
| 肖像、动作或强戏剧性 | `anime-graphite`、`ballpoint-scribble`、`manga-screentone`、`editorial-portrait` | 优先保护面部、姿态和主体外轮廓，`experimental` 风格先做局部测试。 |

命令行可用 `whiteboard list-styles` 查看完整注册元数据，或用
`whiteboard recommend-styles story.md --limit 5` 根据文案关键词获得本地、确定性的候选排序。
视觉风格的来源、命名原则和自定义方式见
[视觉风格来源与自定义说明](visual-style-sources.md)。

## 生成方式与复现

- **原图模式**：30 张原图分别由 Codex 内置图片生成工具生成，一次调用只生成一种
  风格；没有使用参考图输入或后处理滤镜。图片生成具有随机性，因此这些文件是用于
  选型和回归观察的参考样张，不是像素级 golden snapshot。
- **提示词构成**：每次调用都复用上面的统一标准分镜与生产约束，再从对应注册项读取
  `aesthetic`、`paper`、`palette`、`planner_guidance` 和 `avoid`。硬约束要求无文字、
  无水印、无额外人物、无残缺物体，并保持主角簇与幻景簇自然分离。
- **资产处理**：生成结果以 PNG 原样复制到本目录；原图不加标签。联系表由 Pillow
  使用 contain + letterbox 排版，绝不裁切原图，风格编号和 ID 才在这一阶段本地写入。

30 张原图到齐后，可从仓库根目录重复生成五张 1920×1080 联系表：

```bash
python scripts/build_style_contact_sheets.py
```

也可指定其他输入和输出目录：

```bash
python scripts/build_style_contact_sheets.py \
  --input-dir docs/assets/style-gallery/little-match-girl \
  --output-dir docs/assets/style-gallery/little-match-girl
```

脚本会先验证注册顺序中的 30 张原图是否全部存在；只要缺少一张，就会在写出任何
联系表之前失败并列出缺失文件。
