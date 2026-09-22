# 设计：纯文本 pre-pass（OCR + JEV 替代图片/音频输入）→ 自动词库

> 状态：提案，未实施。实施后把决策摘要回写 `HISTORY.md`。
> 日期：2026-09-22

## 一句话目标

在不改 mai-flow 主干（ASR → atoms → merge → translate）的前提下，把 grillmaster
式 pre-pass 的**图片 + 音频输入换成 OCR 文字 + 字幕文本**，让 Gemini 只做纯文本
推理，产出一份**规范化的 `原文 -> 写法` 自动词库**，使翻译在没有人工词库时也能
全片译名一致，且 pre-pass 成本比多模态方案低一个量级。

## 定位

```text
grillmaster pre-pass（对照）
  代表帧图片 + 完整音频 + 完整 SRT → Gemini（多模态，一次）→ 词库 + 摘要

mai-flow（基础架构，不动）
  视频 → ffmpeg → MAI ASR → atoms → merge(LLM) → translate(LLM) → SRT
  实体来源：静态词库；pre-pass 形态：每个 translate batch 在 prompt 内做预分析

mai-flow-jev（本分支，只加一条支线）
  视频 → 代表帧 → [外部 OCR] → JEV 筛选 ──┐
  merge 产出的日文行（纯文本，无时间轴）──┴→ pre-pass(Gemini, 纯文本, 一次) → glossary.md
                                                                                  ↓
                                                                        只注入 translate
```

OCR 的职责是**把「看图认字」从 Gemini 手里拿走**：屏幕上的人名字卡、节目
标题是汉字写法的权威来源，ASR 只会给假名或错字。JEV 的职责是把 OCR 结果中的
效果字、对白字幕、噪声在进 Gemini 之前丢掉。pre-pass 本身仍读完整字幕文本，
这一点与 grillmaster 一致——否则只在对话里出现、从未上屏的实体会漏掉。

支线只有一个出口：`glossary.md`。它只进 translate 的 system prompt。
merge 不接触任何 OCR 内容。

## 现状与差距

### 1. merge 被注入了 OCR 上下文，破坏了原版独立性

jev 分支给 `segment_llm.py` 的 `_llm_merge_batch` / `_merge_one` /
`merge_utterances` 各加了 `context` 参数，把完整 `ocr_context.txt` 拼进每个
batch 的 user prompt，并写进 `merge_cache` 的缓存键。原版 mai-flow 这三个函数
都没有 `context`，缓存键只有 atoms。

后果：每个 merge batch 多花 token；OCR 一变 merge_cache 全部失效；断句决策
和人名怎么写本来无关。

**决定：恢复原版。** 三处 diff 还原，`pipeline.run` 不再向 `merge_utterances`
传 `context`。

### 2. translate 拿到的是「原始 OCR 字串 + 标签」，不是译法

现在 `jev.render_context()` 输出：

```text
[12.300-14.800] person_name: 中川智尋 (source=ocr; confidence=0.93)
[130.000-131.500] person_name: 中川智导 (source=ocr; confidence=0.81)
[301.200-303.000] program_title: そこ曲がったら、櫻坂？ (source=ocr; confidence=0.97)
```

它告诉翻译模型「这是人名」，没告诉「怎么写」。归并变体、决定译法，留给
每个 1000 行 batch 独立完成，2 个 batch 并行互不可见。

对照 grillmaster 的 pre-pass 产出（`services/gemini/pre_pass.py`）：

```python
class PrePassResult(BaseModel):
    characters: list[Character]     # name_jp, name_zh, role_note
    proper_nouns: dict[str, str]    # jp → zh
    glossary: dict[str, str]        # jp → zh
```

grillmaster 让 chunk 翻译一致的关键，就是 pre-pass 产出的是 **jp → zh 映射**，
所有 chunk 共享同一份。`HISTORY.md` #3 用「同一人物不同 chunk 译名不一致」否决
了 per-chunk pre-pass，但这个问题原样留在了 translate 的 batch 里。

**决定：补上 glossary 阶段。** 输入只有 JEV 筛出的几十条 anchor，不是全片
SRT，所以这不是被否决的「全片 Gemini pre-pass」。

### 3. 缓存键缺 model（P0，顺手修）

- `merge_cache` 键：atoms（恢复原版后）— 缺 `segment_prompt.md` 内容和 model。
- `zh_cache` 键：body + system + context — 缺 model。

换 `SEGMENT_MODEL` / `TRANSLATE_MODEL` 或改 `segment_prompt.md` 会静默复用旧
输出。`HISTORY.md` #6 只给 translate 加了 system，是同一类问题修了一半。

**决定：两个阶段的缓存键统一为 `hash(body + system + model)`。** README 里
「修改 segment_prompt.md 后手动删 merge_cache」段落随之删除。

### 4. chunk 边界误插 ` -`（P0，顺手修）

`_format_atom` 给 LLM 看的 speaker 是 `.split("_")[-1]` 后的 `s1`，但
`merge_utterances` 渲染时比较完整 `c00_s1` vs `c01_s1`。LLM 判「同一人」合并，
渲染器判「不同人」插 ` -`。每个 chunk 边界（约 5 分钟一次）只要 gap < 2s
就触发。

**决定：渲染时用与 `_format_atom` 相同的归一化 speaker 比较。** 跨 chunk
speaker 标签本来不可信（`merge.py` docstring 已说明），归一化后的行为是
「同标签视为同一人」，与 LLM 看到的一致。

## 目标数据流

```text
work_dir/
  audio.wav, chunks/, asr.json, out.srt          ← 不动
  reference_frames/                               ← 不动（--extract-frames）
  [外部 OCR] → ocr.json                           ← 用户传 --ocr-json
  jev_cache/, ocr_classification.json            ← 不动（JEV 分类）
  prepass_cache/, glossary.md                     ★ 新增
  merge_cache/, out_llm_ja.srt                    ← merge 恢复原版，不看 OCR
  zh_cache/, out_zh.srt                           ← translate 注入 glossary.md
```

## 阶段契约

### JEV 筛选（已有，角色不变）

- 输入：`ocr.json` 的 observation 列表。
- 输出：每条 `choice` + `confidence`。
- 角色：**廉价前置过滤**，目的是让进 Gemini 的内容尽可能少。只放行
  `person_name / program_title / place_or_brand` 且 `confidence >= 0.75`，
  以及低置信度待复核项。效果字、对白字幕、噪声在这里就丢掉。

### pre-pass → glossary（新增）

- 输入（两部分，都是纯文本）：
  1. JEV 放行的 OCR anchor 列表（文本、类别、首次出现时间、出现次数）——
     提供**权威汉字写法**；
  2. merge 产出的全部日文行（`id<TAB>text`，无时间轴）——提供**全文语境**，
     覆盖只在对话中出现的实体。
- **不输入图片，不输入音频。** 这是与 grillmaster pre-pass 的唯一差别，也是
  省 token 的全部来源。
- 一次 LLM 调用。默认模型走 `PREPASS_MODEL`，未设置时用 `TRANSLATE_MODEL`。
- 任务：
  - 从全文中识别人名、节目/作品名、地名/品牌；
  - 用 OCR anchor 校正写法（ASR 的假名/错字 → 屏幕上的汉字）；
  - 归并变体（`中川智导 / 中川智尋 / 中 智 / ちーたん` → 一条）；
  - 丢弃明显不是实体的项。
- **写法规则与 translate 的风格规则一致**（见下文「translate 风格规则」）：
  - 人名：保留原文官方汉字写法，**不做简繁转换**（`髙橋` 不变 `高桥`）；
    假名人名若能确定对应汉字则给汉字，否则保留假名。
  - 节目名 / 作品名：保留原文日文写法。
  - 地名 / 品牌：有通行中文译法则给，否则保留原文。
- 输出 JSON：

```json
{"entries": [{"src": ["中川智尋", "中川智导", "中 智"], "canonical": "中川智尋", "kind": "person_name"}]}
```

- 程序渲染为 `glossary.md`，格式与 `TRANSLATE_GLOSSARY_PATH` 完全相同：

```text
中川智尋 -> 中川智尋
中川智导 -> 中川智尋
中 智 -> 中川智尋
そこ曲がったら、櫻坂？ -> そこ曲がったら、櫻坂？
```

  每个 `src` 变体各占一行指向同一 `canonical`，翻译模型不需要自己做归并。
  对人名和节目名，这份词库的价值主要是**变体归并 + 纠正 OCR/ASR 错字**，
  而不是「翻译」。
- 缓存：`prepass_cache/{hash(anchors + ja_lines + system + model)}.json`。
  OCR、字幕文本、prompt 都不变就不重跑。
- 运行条件：有 `--ocr-json` 时运行。没有 OCR 时是否仍以纯字幕文本跑 pre-pass
  留为开关（`--prepass`），默认关——那样就退化成 mai-flow 的 in-prompt 预分析
  的共享版本，与本分支的实验目的无关。

### translate（改注入内容）

system prompt = `OUTPUT_CONTRACT` + 自动词库（`glossary.md`）+ 外部词库
（`TRANSLATE_GLOSSARY_PATH`，可选）。

- 两份词库走**同一条注入路径**（现有的「可选外部术语表」段落），不新增概念。
- 冲突时外部词库优先：外部是人工确认的，自动是模型猜的。实现上把外部词库
  放在自动词库之后，并在段落说明里声明优先级。
- **不再注入原始 `ocr_context`。** 它的信息已经被 glossary 覆盖，且更短。
- 缓存键：`hash(body + system + model)`，system 已含两份词库。

### translate 风格规则（恢复通用版 `translate_prompt.md`）

jev 分支把原版 22.8KB 的 `translate_prompt.md` 整个删掉，只剩 `OUTPUT_CONTRACT`。
原版内容分三类，只有前两类该删：

| 类别 | 内容 | 处理 |
|---|---|---|
| A 坂道专属 | 角色设定、成员昵称例子、静态词库 60 行、`_NAME_FIXES` | 删；词库走 `TRANSLATE_GLOSSARY_PATH` |
| B 职责错位 | 预分析报告、emoji/超链接还原、` -` 插入规则、时间轴检测、（※）注释框架 | 删；HISTORY #4 理由成立 |
| C 通用规则 | 见下 | **错删了，恢复为精炼通用版** |

C 类需保留的规则（去掉所有坂道例子）：

- 行边界固定、每行约 20 字（优化目标非强制）
- 流水协议：预读 N+1 行、意群优先、避免「的/了/就」开头的孤儿词
- 再现心境、补完省略、情景还原
- 标点：句末去「。，」、句中逗号→空格、引号→「」（与 `_clean_zh` 一致）
- 引用/转述内容用「」括起
- 高频词按语境译潜台词
- 称呼：假名称呼若对应【术语表】中的汉字名则转汉字；ちゃん/さん/たん 后缀
  译法；非主要人物有汉字用汉字、否则罗马字
- 人名保留原文汉字写法、禁止简繁转换（`髙`≠`高`、`﨑`≠`崎`）
- 语气词省略列表（えっと/あの/なんか/まあ/はい/うん…）+ 译文禁出现「啊嗯欸」
- 作品名保留原文日文写法
- ASR 错字按【术语表】修正为最可能的实体名

其中「称呼」「人名保留」「错字修正」三条在原版依赖静态词库，通用版统一依赖
【术语表】段落——即 `glossary.md`（自动）+ `TRANSLATE_GLOSSARY_PATH`（人工）
注入的位置。这是 pre-pass 与翻译规则的唯一接口。

`_load_system = translate_prompt.md + 术语表段落 + OUTPUT_CONTRACT`，与原版
结构一致。`_clean_zh` 恢复 `_BANNED_CHARS`，不恢复 `_NAME_FIXES`。
HISTORY #4 措辞改为「剥离坂道专属和流程约束，保留通用翻译规则」。

### merge（恢复原版）

- `_llm_merge_batch(atoms, start_idx, end_idx)`
- `_merge_one(atoms, start, end, cache_dir)`
- `merge_utterances(atoms, cache_dir)`
- 缓存键：`hash(atoms_body + system + model)`。

## Token / 速度账（估算，实施后用 usage 实测替换）

### pre-pass 一次调用的输入：多模态 vs 纯文本

按 Gemini 公开计费口径（音频 32 tok/s；图片每 768×768 tile 258 tok），
1h 视频、31 张代表帧：

| 输入 | grillmaster（多模态） | jev（纯文本） |
|---|---|---|
| 音频 | 3600 × 32 ≈ 115k | 0 |
| 图片 | 31 × 258~516 ≈ 8~16k | 0 |
| OCR 文字（JEV 筛后） | 0 | 几百 |
| 完整字幕文本（~1000 行） | ~15~20k | ~15~20k |
| **合计** | **~140~150k** | **~16~21k** |

约 7 倍差距；纯文本调用的延迟也明显低于带 1h 音频的多模态调用。

### 与 jev 分支现状的对比

设 OCR 上下文 C 行，merge M 批，translate T 批，glossary G 行（G 通常远小于
C，因为变体归并为一行、噪声已丢弃）。

| | 现在 | 方案后 |
|---|---|---|
| merge 注入 | C × M | 0 |
| translate 注入 | C × T | G × T |
| pre-pass 调用 | 0 | 1 次，输入 ≈ 全文 + C |

方案后多了一次全文输入（~20k），换来：merge 完全不碰 OCR、translate 每批只
注入几十行 glossary、全片译名一致。pre-pass 只依赖 merge 产出的日文行和 OCR，
与 translate 串行；本轮不做并行。

## 任务清单

- [ ] T1 merge 恢复原版独立性
  文件：`flows/maijev/segment_llm.py`、`flows/maijev/pipeline.py`
  约束：三个函数签名与原版 mai-flow 一致；`pipeline.run` 不传 context
  验收：`diff` 原版与 jev 分支的 `_llm_merge_batch/_merge_one/merge_utterances`
  只剩缓存键那一处差异

- [ ] T2 缓存键统一为 body + system + model
  文件：`segment_llm.py`、`translate_llm.py`
  约束：不改缓存文件布局（仍是 `batch_{hash}.json`）
  验收：改 `segment_prompt.md` 或环境变量 model 后，旧缓存不命中

- [ ] T3 chunk 边界 ` -` 修复
  文件：`segment_llm.py`（`merge_utterances` 渲染分支）
  约束：与 `_format_atom` 共用同一个 speaker 归一化函数
  验收：构造跨 chunk 同标签 atom 组，渲染结果不含 ` -`

- [ ] T4 新增 `prepass.py`
  文件：`flows/maijev/prepass.py`（新）、`flows/maijev/prepass_prompt.md`（新）
  约束：stdlib + 现有 `llm.generate_json`；输入是 JEV 结果 + `MergedLine` 列表，
  不直接读 OCR、不读图片、不读音频；输出格式与 `TRANSLATE_GLOSSARY_PATH` 完全
  一致；prompt 不含任何坂道词汇
  验收：给定 anchor + 日文行 → `glossary.md`；重跑命中缓存不再调 LLM；
  记录 usage 的 prompt_tokens 以验证上表估算

- [ ] T5 translate 换注入内容 + 恢复通用版提示词
  文件：`translate_llm.py`（`_load_system`、`_clean_zh`）、`pipeline.py`、
  `flows/maijev/translate_prompt.md`（新，通用版）
  约束：删除 `context` 参数；自动词库 + 外部词库同一【术语表】段落注入，外部
  优先；提示词不含任何坂道词汇；`_BANNED_CHARS` 恢复
  验收：`zh_cache` 键随 `glossary.md` 变化；不再出现 `【OCR 上下文】` 段；
  `grep -c '坂\|乃木\|櫻\|日向' translate_prompt.md` 为 0

- [ ] T6 文档同步
  文件：`README.md`、`flows/maijev/README.md`、`HISTORY.md`
  验收：删除「手动删 merge_cache」段落；HISTORY 新增本次决策摘要

- [ ] T7 纯函数测试（首批）
  文件：`tests/test_segment_llm.py`、`tests/test_prepass.py`（新）
  范围：`_coalesce_un_atoms`、group 校验、speaker 归一化、glossary 渲染
  约束：不调 LLM、不调 ffmpeg

## 不做的事

- 不改 ASR / chunker / atoms。
- 不给 merge 任何外部上下文。
- 不做 async / provider 抽象 / httpx 替换。
- 不给 pre-pass 输入图片或音频（`HISTORY.md` #3 的否决对象修正为「多模态
  pre-pass」；纯文本 pre-pass 读全文是本方案的核心，不在否决范围内）。
- 不保留 translate prompt 内的每批预分析（pre-pass 已共享化，两者并存是重复）。
- 不在仓库内置任何词库；`glossary.md` 是 work_dir 里的运行产物。

## 待定问题

1. **汉字人名是否需要 LLM？** 日本汉字人名进中文字幕大多只是繁→简（`尋 → 寻`），
   可用 OpenCC 确定性完成；只有假名人名和节目名真正需要 LLM。可作为 glossary
   阶段的后续优化：汉字项走 OpenCC，其余走 LLM，再省一次调用。（假设，未验证
   OpenCC 对人名用字的覆盖。）
2. **glossary 是否收录 `place_or_brand`？** 默认收录，但地名/品牌译法争议少，
   可以按需关闭。
3. **是否合回 `mai-flow-open`？** T1–T3 是纯修复，与 OCR 无关，适合合回主干；
   T4–T5 是 jev 支线，留在本分支。
