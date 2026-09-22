# maijev 演进记录（原 mai-flow-jev 分支）

> 本文记录这个项目**试过什么、否决了什么、为什么是现在这个样子**。
> 架构用法看 `README.md`；这里只保留决策依据和实测数字。

## 当前形态（2026-09-22，第二轮）

```text
视频
  → ffmpeg 抽音频 → MAI-Transcribe-2 ASR（OpenRouter）
  → 词级 atomize → Gemini 2.5 Pro 合并字幕行（与 mai-flow 主干一致，不看 OCR）
  → Gemini 2.5 Pro 翻译（1000 条/批，并发 2，注入通用风格 prompt + 术语表）
  → 程序恢复时间轴 → out_zh.srt

并行支线（可选）：
  视频 --extract-frames → 稀疏代表帧（grillmaster 逻辑）
    → 外部 OCR 工具 → --ocr-json 传回
    → JEV 分类筛选（person_name / program_title / place_or_brand / review）
    → 纯文本 pre-pass：OCR anchor + 全日文行 → Gemini 一次 → glossary.md
    → glossary.md 注入每个翻译批次的【术语表】段落
```

核心原则：**人名、节目名不靠翻译 prompt 猜，靠 OCR + 共享 pre-pass 提供**。
LLM 只做语义工作（断句合并、词库归并、翻译），时间轴永远由程序掌握。
设计全文见 `docs/prepass_glossary_design.md`。

## 关键决策与被否决的方案

### 1. ASR：MAI-Transcribe-2（OpenRouter）

2026-09-20 实测（macOS `say` TTS 音频，自动语言检测）：

| 输入 | 延迟 | 结果 |
|---|---|---|
| 英文 ~9.5s | 1.2s | 全文正确，`MAI-Transcribe 2`、`OpenRouter` 拼写正确（phraseList 生效） |
| 日文 ~5.8s | 1.1s | 自动检测日文，`櫻坂46` 正确 |

已验证能力：`verbose_json` 段落 + `timestamp_granularities:["word"]` 词级时间戳、
`diarization.enabled` speaker 标签、`phraseList.phrases` 专有名词偏置、
`transcribeStyle:"clean"`。日文 word 时间戳是逐字的（`こ` `ん` `に`…），属预期。

### 2. 否决：autoscreen 式全帧 OCR

原 autoscreen 路线实测（早期会话记录，`jev-chokosaku-246` 的 `run.json`，文件已清理）：

```text
fps=0.5 → 508 帧 → 约 509 次 PaddleOCR API 调用 → 1029 条 JEV 分类
```

1016 秒视频的全量画面文字索引，成本高、速度慢。人名识别不需要这个密度。

### 3. 否决：独立 Gemini pre-pass

grillmaster 的结构是「抽帧 + 音频 + 完整 SRT → Gemini 一次 pre-pass → 分块翻译复用」。
本项目里 **OCR + JEV 已经承担了 pre-pass 的职责**（产出人名/节目名/专有名词），
再加一个 Gemini pre-pass 是重复劳动。最终结构：

```text
代表帧 → OCR → JEV 分类   = pre-pass（非 LLM）
Gemini 2.5 Pro 分块翻译    = 唯一直接消费上下文的 LLM 阶段
```

也否决了「每个 chunk 各自 pre-pass」：同一人物在不同 chunk 会得到不同译名。

### 4. 否决：translate_prompt.md 的「预翻译分析」

旧 `translate_prompt.md` 要求模型每批先输出【预分析报告】（动态术语表、人名识别），
再翻译。问题：

- 人名识别本该由 OCR/JEV 完成，prompt 里再做一遍是职责错位；
- 分批翻译时每批独立预分析，术语表可能不一致；
- prompt 长达 15.5KB，大部分是与输出无关的流程约束。

2026-09-22 已删除该文件。翻译 system prompt 现在只有：输出契约（JSON 格式 +
id 对齐 + ` -` 说话人分隔符保留）+ 可选外部术语表 + JEV OCR 上下文。

`segment_prompt.md` **保留**——它是断句合并的规则，不承担人名识别，职责不同。

### 5. 抽帧：移植 grillmaster 的验证逻辑

`flows/maijev/frames.py`，参数与 grillmaster `settings.py` 一致：

| 参数 | 值 | 作用 |
|---|---|---|
| `interval_seconds` | 120 | 绝对时间轴采样间隔 |
| `intro_skip_seconds` | 3.0 | 跳过片头/台标 |
| `max_side` | 768 | 最长边缩放 |
| `last_frame_offset` | 1.5 | 末尾 GOP 安全距离（`-ss` 快定位落在前一关键帧，需留可解码帧） |

保留的语义：`-ss` 在 `-i` 前（快定位）、`include_end` 始终补末帧、
帧文件名 `frame_{ts:010.3f}_{max_side}.jpg` 即缓存键、单帧失败 warn+skip 不中断。

**踩过的坑**：scale 滤镜的 `if(gte(iw,ih),…)` 表达式含逗号，必须加引号：

```text
-vf "scale='if(gte(iw,ih),768,-2)':'if(gte(iw,ih),-2,768)'"
```

不加引号 ffmpeg 报 `No such filter: 'ih)'`（exit 8）。

实测：`译.mp4`（3588s）→ **31 帧**，2.5s 完成，共 1.8MB。
对比 autoscreen 的 508 帧，抽帧量降约 16 倍。

### 6a. 第二轮（同日）：merge 独立性与共享 pre-pass

**merge 恢复原版独立性。** 此前 OCR 上下文同时注入 merge 和 translate，破坏
了 mai-flow 主干的 merge 独立性（原版三函数无 context 参数）。断句决策与实体
写法无关，注入只带来 token 浪费和 merge_cache 随 OCR 失效。已还原。

**共享纯文本 pre-pass 替代每批预分析。** 原版 translate_prompt.md 的「预翻译
分析报告」本质是每批各自做 pre-pass——#3 否决 per-chunk pre-pass 的理由对它
同样成立。新增 `prepass.py`：JEV 筛出的 OCR anchor + merge 后全部日文行，一次
Gemini 调用产出 `glossary.md`（`原文 -> 写法`），注入每个翻译批次。与
grillmaster 的差别只在输入模态：不发图片/音频（1h 视频约省 120k+ token），
全文文本照读——否则只在对话中出现的实体会漏。

**通用版 translate_prompt.md 恢复。** 此前删整个文件把通用规则一起删了：流水
协议（预读 N+1、意群优先、避免孤儿词）、人名保留原文汉字（髙≠高）、敬称后缀
译法、语气词省略、作品名保留原文，这些是领域无关的字幕组规则。已重写为
2.8KB 通用版（无任何坂道词汇），结构 = prompt + 【术语表】+ 输出契约。
`_clean_zh` 恢复 `_BANNED_CHARS`（啊嗯欸），`_NAME_FIXES` 不恢复（由
glossary 接管）。

**缓存键统一 body + system + model。** merge/translate/prepass 三处一致；
`merge_cache` 原来连 system 都没有，改 prompt 或换模型会静默命中旧缓存。

**修复 chunk 边界误插 ` -`。** LLM 看到的 speaker 是归一化后的 `s1`，渲染器
却比较带 chunk 前缀的 `c00_s1` vs `c01_s1`，同一人跨 chunk 合并会被误加说话人
分隔符。渲染改用与 `_format_atom` 相同的 `_speaker_tag` 归一化。

### 6. 缓存键必须含 system prompt

`zh_cache/` 原来只按「字幕行 + OCR 上下文」hash。删除 translate_prompt.md 后
旧缓存会静默返回按旧 prompt 生成的译文。已改为 `body + system + context` 一起 hash，
prompt 变更自动失效，无需手动清缓存。

## 边界（本项目不做什么）

- **不做 OCR 本身**：`--extract-frames` 只产图，OCR 由外部工具完成，
  结果以 JSON 经 `--ocr-json` 传入（格式见 README）。
- **JEV 只做分类不做归并**：它回答「这段文字是不是人名」。归并发生在
  pre-pass 阶段（一次共享调用），产出的 `glossary.md` 给所有翻译批次用，
  而不是让每个批次各自归并。
- **不改原版 mai-flow**：本仓库是 jev 分支，所有改动只在这里。

## 文件变更清单（2026-09-22）

| 文件 | 变更 |
|---|---|
| `flows/maijev/frames.py` | 新增，grillmaster 抽帧移植（subprocess 版，无 ffmpeg-python 依赖） |
| `flows/maijev/pipeline.py` | 新增 `--extract-frames` 开关 |
| `flows/maijev/translate_llm.py` | 移除 translate_prompt.md 依赖；缓存键加入 system hash |
| `flows/maijev/translate_prompt.md` | 删除 |
| `README.md` / `flows/maijev/README.md` | 同步文档 |

## 文件变更清单（2026-09-22，第二轮）

| 文件 | 变更 |
|---|---|
| `flows/maijev/segment_llm.py` | 三函数去 `context` 恢复原版独立性；缓存键加 system+model；新增 `_speaker_tag`，渲染按归一化 speaker 判定 ` -` |
| `flows/maijev/jev.py` | 抽出 `select_anchors()` 供 render_context 与 prepass 共用 |
| `flows/maijev/prepass.py` | 新增，纯文本共享 pre-pass → glossary.md |
| `flows/maijev/prepass_prompt.md` | 新增，通用词库生成 prompt |
| `flows/maijev/translate_prompt.md` | 恢复，2.8KB 通用版（无坂道词汇） |
| `flows/maijev/translate_llm.py` | `_load_system` = prompt +【术语表】（自动+外部）+ 契约；`_BANNED_CHARS` 恢复；缓存键加 model；`context` → `glossary_path` |
| `flows/maijev/pipeline.py` | merge 不传 context；新增 pre-pass 调用与 `--prepass` 开关 |
| `tests/` | 新增，10 个纯函数测试 |
| `pyproject.toml` | dev 依赖 +pytest |
| `docs/prepass_glossary_design.md` | 新增，本设计文档 |
