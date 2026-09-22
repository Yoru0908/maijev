# maijev

`maijev` 是一条面向长视频的日语字幕流水线：

```text
主干（每次运行都执行）：

  视频/音频
    → ffmpeg 抽取音频
    → MAI-Transcribe-2 词级 ASR
    → 静音、标点、speaker 边界拆 atom
    → Gemini 合并字幕行
    → Gemini 翻译中文 ──→ 程序恢复时间轴，输出 SRT
                          ▲
                          │ 注入 glossary.md 自动词库（有支线时）
                          │
可选支线（译名一致性，逐层降级，缺哪层降哪层）：

  完整链路      --extract-frames → 代表帧 → 外部 OCR → ocr.json
                → Jev 分类筛选 ──────────┐
  有 OCR 无 JEV  ocr.json 原文直进 ──────┤→ 纯文本 pre-pass（一次调用：
  无 OCR         --prepass ──────────────┘   OCR 文字 + 全日文行）
                                             → glossary.md → 翻译批次共享
```

项目仓库：

```text
https://github.com/Yoru0908/maijev
```

## 特点

- 长音频自动切片，单个 ASR chunk 可以独立重试。
- ASR、字幕合并和翻译都有本地缓存，支持断点续跑。
- 时间戳始终由程序掌握，LLM 不生成、不修改时间轴。
- LLM 只负责语义任务：字幕怎么合并、日文怎么翻译。
- LLM 输出使用 JSON，程序负责校验、按 id 对齐和生成 SRT。
- 普通短促相槌在进入 merge LLM 前由程序处理，避免翻译阶段产生空字幕行。
- 不内置任何特定节目、成员或项目词库；需要术语一致性时，可以通过外部文件注入。

## 安装

要求：

- Python `>=3.13`
- [`uv`](https://docs.astral.sh/uv/)
- `ffmpeg` 和 `ffprobe`
- OpenRouter API key，用于 MAI ASR
- Google Vertex AI Agent Platform key，推荐用于 Gemini merge/translation

安装依赖：

```bash
cd /path/to/maijev
uv sync
ffmpeg -version
ffprobe -version
```

## API 配置

可以导出环境变量，也可以写入仓库根目录的 `.env`。`.env` 不应提交到公开仓库。

推荐配置：

```bash
# ASR：OpenRouter speech-to-text
export OPENROUTER_API_KEY=sk-or-v1-...

# LLM：Google Vertex AI Agent Platform 直连
export GEMINI_AGENT_PLATFORM_API_KEY=AQ...
# 也支持 AGENT_PLATFORM_API_KEY
```

支持的 LLM 配置：

| 环境变量 | 用途 |
|---|---|
| `GEMINI_AGENT_PLATFORM_API_KEY` | Vertex AI Agent Platform |
| `AGENT_PLATFORM_API_KEY` | Vertex key 兼容别名 |
| `GEMINI_API_KEY` | Google AI Studio |
| `OPENROUTER_API_KEY` | 没有 Gemini key 时的 LLM fallback；同时用于 ASR |
| `GEMINI_AGENT_PLATFORM_BASE_URL` | 可选的 Vertex publisher endpoint |
| `GEMINI_MODEL` | 全局 Gemini 模型默认值 |
| `SEGMENT_MODEL` | merge 阶段模型覆盖值 |
| `TRANSLATE_MODEL` | translation 阶段模型覆盖值 |
| `LLM_MAX_OUTPUT_TOKENS` | LLM 最大输出 token，默认 `65536` |
| `TRANSLATE_GLOSSARY_PATH` | 可选外部术语表路径 |
| `PREPASS_MODEL` | pre-pass（词库生成）阶段模型覆盖值 |

Jev OCR 上下文（可选；仅 `--ocr-json` 时用到）：

| 环境变量 | 用途 |
|---|---|
| `CLOUDFLARE_ACCOUNT_ID` | Cloudflare account ID |
| `CLOUDFLARE_API_TOKEN` | 调用 Workers AI Jev 的 API token |

没有 Cloudflare 凭据时 `--ocr-json` 仍可用：跳过 JEV 分类，OCR 原始文字
直接进 pre-pass。同理 `--prepass` 可在没有任何 OCR 时单独运行。

LLM 后端选择顺序：

```text
GEMINI_AGENT_PLATFORM_API_KEY / AGENT_PLATFORM_API_KEY
  → Vertex AI Agent Platform

GEMINI_API_KEY
  → Google AI Studio

OPENROUTER_API_KEY
  → 仅在没有 Gemini 配置时作为 LLM fallback
```

ASR 始终使用 OpenRouter 的 speech-to-text endpoint，与 LLM 后端相互独立。

## 快速开始

### 只生成确定性基线字幕

```bash
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example
```

输出：

```text
runs/example/out.srt
```

### 生成 LLM 断句后的日文字幕

```bash
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --llm-segment
```

输出：

```text
runs/example/out.srt          # 确定性基线
runs/example/out_llm_ja.srt   # LLM 合并后的日文字幕
```

### 生成日文和中文字幕

`--translate` 会自动启用 `--llm-segment`：

```bash
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --translate
```

输出：

```text
runs/example/out_llm_ja.srt
runs/example/out_zh.srt
```

### 可选：视觉上下文支线（OCR → pre-pass → 自动词库）

整条支线是可选的，而且**逐层降级**——有什么凭据就走哪一层：

```text
有 OCR 工具 + Cloudflare/JEV 凭据（完整链路）：
  ocr.json → Jev 分类筛选（丢弃效果字/对白/噪声）
           → 筛出的实体进 pre-pass

有 OCR、无 Cloudflare 凭据：
  ocr.json → OCR 原文不筛选，直进 pre-pass

无 OCR，仅加 --prepass：
  字幕全文单独进 pre-pass

什么都不加：
  不初始化 Jev、不跑 pre-pass、不要求任何额外凭据
```

无论走哪层，终点都是同一次**纯文本 pre-pass**：Gemini 收到 OCR 文字（筛过
或原文）+ merge 产出的全部日文行，把实体变体归并成 `glossary.md` 自动词库。
它代替了逐批的内部预分析——每个翻译批次注入的是同一份词库，而不是各自从
原始 OCR 文字猜测写法。字幕合并（merge）阶段不接收任何 OCR 内容，与 mai-flow
主干完全一致。

OCR 本身不在本仓库实现：`--extract-frames` 只负责抽帧，OCR 由你自己的工具
完成，结果经 `--ocr-json` 传回。

#### 稀疏代表帧抽取

使用 `--extract-frames` 按 grillmaster 验证过的策略抽取少量代表帧：

- 跳过开头约 3 秒，避开电视台片头/台标；
- 每 120 秒抽一帧；
- 末尾预留 1.5 秒安全距离，避免快定位落在最后 GOP；
- 等比缩放到最长边 768px；
- 帧文件和 manifest 缓存到 `work_dir/reference_frames/`。

```bash
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --extract-frames \
  --translate
```

输出：

```text
runs/example/reference_frames/
├── frames/                    # JPEG 帧文件
└── frames_manifest.json       # 帧时间戳和路径清单
```

这些代表帧可以送给外部 OCR 工具，再把 OCR 结果通过 `--ocr-json` 传回——有
Cloudflare 凭据会先经 Jev 分类筛选，没有则原文直进 pre-pass。

输入支持以下三种 JSON 形状：

```json
[
  {
    "track_id": "ocr-001",
    "text": "武元唯衣",
    "first_seen": 12.3,
    "last_seen": 14.8,
    "ocr_score": 0.98
  }
]
```

也支持 `{"items": [...]}` 或 `{"ocr_tracks": [...]}`。每个 observation 至少需要
`item_id`、`track_id` 或 `id` 之一，以及非空的 `text`；时间、OCR 分数和 `bbox`
为可选字段。

```bash
# 可选：有 Cloudflare 凭据才导出；没有也能跑，OCR 原文直进 pre-pass
export CLOUDFLARE_ACCOUNT_ID=...
export CLOUDFLARE_API_TOKEN=...

uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --ocr-json ocr_observations.json \
  --translate
```

该阶段输出：

```text
runs/example/jev_cache/               # Jev 分类响应缓存
runs/example/ocr_classification.json  # 每个 OCR observation 的分类
runs/example/ocr_context.txt          # Jev 筛选结果（调试观察用）
runs/example/prepass_cache/           # pre-pass 响应缓存
runs/example/glossary.md              # pre-pass 产出的自动词库
```

没有 `--ocr-json` 也没有 `--prepass` 时，整条支线不运行，也不需要任何额外
凭据。


### 指定基线 SRT 路径

`--srt` 只控制确定性基线 `out.srt` 的路径；LLM 输出仍写入工作目录。

```bash
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --srt outputs/example_base.srt \
  --translate
```

输入可以是视频或音频，实际音频格式由 ffmpeg 读取。建议每个输入文件使用独立的
工作目录。

## 工作目录和缓存

完整运行后的目录结构：

```text
work_dir/
├── audio.wav                 # 16kHz、单声道、PCM 音频
├── chunks/
│   ├── manifest.json         # chunk 起止时间和文件列表
│   ├── chunk_00.wav          # 静音点对齐的约 5 分钟音频
│   ├── chunk_00.wav.json     # MAI ASR 原始响应缓存
│   └── ...
├── asr.json                  # 合并后的统一 ASR payload
├── out.srt                   # 确定性基线 SRT
├── reference_frames/         # 稀疏代表帧 + manifest（--extract-frames，可选）
├── jev_cache/                # Jev OCR 分类的 per-batch JSON 缓存（可选）
├── ocr_classification.json   # Jev 分类结果（可选）
├── ocr_context.txt           # Jev 筛选结果（调试观察用，可选）
├── prepass_cache/            # pre-pass 响应缓存（可选）
├── glossary.md               # pre-pass 产出的自动词库（可选）
├── merge_cache/              # merge LLM 的 per-batch JSON 缓存
├── out_llm_ja.srt            # LLM 合并后的日文 SRT
├── zh_cache/                 # translation LLM 的 per-batch JSON 缓存
├── out_zh.srt                # 中文字幕
└── timings.json              # 阶段耗时
```

缓存原则：

- `chunks/*.wav.json` 存在时，不重复调用对应的 ASR 请求。
- `merge_cache/` 按 atom 内容 + system prompt + model hash 缓存合并结果。
- `zh_cache/` 按字幕行内容 + system prompt（含两份术语表）+ model hash 缓存
  翻译结果。
- `prepass_cache/` 按 OCR anchor + 日文行 + system prompt + model hash 缓存。
- prompt、模型或词库变更都会使对应缓存自动失效，无需手动删除。
- 已完成的 ASR chunk 不需要删除。

只重新翻译：

```bash
rm -rf runs/example/zh_cache
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --translate
```

## 流水线阶段

### 1. 音频抽取和切片

`chunker.py` 使用 ffmpeg 抽取 16kHz 单声道 PCM，再使用 `silencedetect` 找静音点，
把长音频切成约 5 分钟的 chunk。切点优先落在目标时间附近的静音区，并记录每个
chunk 相对于完整音频的起始偏移。

### 2. MAI-Transcribe-2 ASR

`transcriber.py` 调用：

```text
POST https://openrouter.ai/api/v1/audio/transcriptions
model: microsoft/mai-transcribe-2
```

请求包括：

- `response_format=verbose_json`
- `timestamp_granularities=["word"]`
- speaker diarization
- 可选 phrase list biasing
- `transcribeStyle=verbatim`

默认 phrase list 为空，不包含任何领域名称。需要专有名词 biasing 时，可以在调用
`build_payload()` 或 `transcribe_chunk()` 时传入自己的 `phrases` 列表。

ASR 对 429、500、502、503、504 等临时错误自动退避重试，成功响应写入 chunk
旁边的 JSON 缓存。

### 可选：Jev OCR 分类与纯文本 pre-pass

`--ocr-json` 阶段只读取外部 OCR observation，并把每个 observation 放进 Jev
`state.items`，通过 `choice` 问题分类为人名、节目名、地点/品牌、普通对白、效果
文字、噪声或未知。分类响应按完整请求体 hash 缓存到 `jev_cache/`。

随后 `prepass.run_prepass()` 把 JEV 筛出的 anchor（`jev.select_anchors` 筛选：
高置信度实体 + 低置信度待复核项）与 merge 产出的全部日文行一起发给 Gemini
做**一次共享的纯文本调用**，产出 `glossary.md` 自动词库（`原文 -> 写法`，与
`TRANSLATE_GLOSSARY_PATH` 同格式）。这个词库——而不是原始 OCR 文字——注入每个
翻译批次，所以全片译名一致。

字幕合并阶段不接收 OCR 内容。没有 `--ocr-json` 时 Jev 和 pre-pass 都不会被
调用；`--prepass` 可在无 OCR 时单独启用 pre-pass。


### 3. Atom 拆分

`segment_llm.build_atoms()` 按 word-level 时间戳切分 atom：

- speaker 切换
- 词间静音至少 1 秒
- 硬标点：`。！？?!`
- 软标点：`、，,：:；;`
- 单个 atom 超过 48 字时的长度兜底

基础边界来自时间戳，LLM 不参与这一阶段。

#### 短促相槌预处理

普通 `うん` 和重复形式在进入 merge LLM 前确定性处理：

- 同 speaker 且相邻间隙不超过 2 秒：并入邻句。
- 跨 speaker 且没有同 speaker 邻句：并入时间间隙更小的邻句，并保留 ` -`
  speaker 分隔符。
- 不同 speaker 的近距离 `うん / うん` 保留为独立 atom，以显示对话轮换。
- 超过 2 秒的长停顿不强行合并。

这不是领域词库，而是字幕结构处理，用于避免纯相槌在翻译后变成空字幕。

### 4. LLM 合并

`segment_llm.merge_utterances()` 按 batch 发送 atom。当前配置：

```text
BATCH_SIZE = 1000 atoms
MAX_WORKERS = 2
CONTEXT_TAIL = 20 atoms
```

LLM 输入格式：

```text
id | speaker | gap | text
```

LLM 只返回合并关系：

```json
{"groups": [[0, 1], [2, 3, 4]]}
```

程序收到 groups 后会：

1. 校验 id 是否连续、递增、未重复。
2. 拒绝跨越过长静音的 group。
3. 根据 group 首尾 atom 恢复 `start` 和 `end`。
4. 保留未被 group 使用的 atom。

LLM 不接收也不生成最终 SRT 时间戳。如果 merge batch 连续失败，程序直接报错，
不会把失败 batch 静默伪装成成功结果。

### 5. 中文翻译

翻译阶段接收已经合并后的日文字幕行：

```text
id<TAB>日文字幕文本
```

翻译 LLM 返回：

```json
{
  "lines": [
    {"id": 0, "zh": "中文译文"},
    {"id": 1, "zh": "下一行译文"}
  ]
}
```

程序按 `id` 对回 `MergedLine`：

```text
MergedLine.start + MergedLine.end + translated zh
  → render_srt()
  → out_zh.srt
```

翻译 LLM 不负责输出时间轴；时间轴来自 ASR word 时间戳和程序的 atom/group 边界。

翻译配置：

```text
BATCH_SIZE = 1000 lines
MAX_WORKERS = 2
maxOutputTokens = 65536
```

确定性后处理包括：

- 校验每个输入 id 是否都有返回。
- 清理多余标点和格式。
- 保留 ` -` speaker 分隔符。
- 空译文回退到对应原文，避免静默丢行。

## 外部术语表

开源版本不内置任何词库。如果某个项目需要固定人名、作品名或专有名词译法，可以
在仓库外准备一个普通文本文件：

```text
原文术语 -> 固定译法
另一个术语 -> 另一个译法
```

运行前设置：

```bash
export TRANSLATE_GLOSSARY_PATH=/path/to/private-glossary.md
```

程序会把该文件追加到 translation system prompt，且不会把它复制到仓库、缓存或
Git 历史。不开启该变量时，翻译完全使用通用 prompt 和当前批次上下文。

## Prompt 实验台

只查看 atom：

```bash
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --start 0 \
  --end 120 \
  --atoms
```

调用 merge LLM 测试 0–120 秒：

```bash
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --start 0 \
  --end 120
```

指定自定义断句 prompt 和模型：

```bash
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --system my_segment_prompt.md \
  --model gemini-2.5-pro
```

修改 prompt 后，正式 pipeline 运行前应清理对应缓存。

## 故障排查

### `OPENROUTER_API_KEY not set`

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
```

### LLM 请求失败或超时

检查 Gemini key、模型名称、网络连接和 `LLM_MAX_OUTPUT_TOKENS`。删除未完成的
`merge_cache/` 或 `zh_cache/` 后重新执行；已完成的 ASR chunk JSON 不需要删除。

### ASR chunk 遇到 429

代码会自动退避重试。不要同时启动多个相同长视频任务；重新运行时会复用已经成功
的 chunk 缓存。

### 修改 prompt 后结果没有变化

缓存键已包含 system prompt 和 model，prompt/模型/词库变更会自动失效。如果
仍想强制重跑：

```bash
rm -rf runs/example/merge_cache
rm -rf runs/example/zh_cache
rm -rf runs/example/prepass_cache
```

### 日文和中文字幕行数不一致

```bash
grep -cE '^[0-9]+$' runs/example/out_llm_ja.srt
grep -cE '^[0-9]+$' runs/example/out_zh.srt
cat runs/example/timings.json
```

翻译阶段会对空译文回退到原文。renderer 仍会跳过只含标点、没有可显示文字的块。

## 当前限制

- speaker 编号只保证在单个 ASR 请求范围内有效；跨 chunk 不保证同一个人继续使用
  同一个编号。严格的全片 speaker identity 需要额外的全局 diarization 或 speaker
  embedding。
- LLM 的响应速度和最大输出长度取决于具体模型和后端；`65536` 是配置上限，不
  代表每次调用一定生成这么多 token。
- phrase list 需要调用方按领域自行传入，不由仓库维护。

## GitHub 版本管理

建议流程：

```bash
git status
git diff --check
uv run python -m py_compile flows/maijev/*.py
git add .
git commit -m "fix: describe change"
git push origin main
```

commit message 使用以下前缀：

```text
feat: 新功能
fix: 修复行为
config: 配置变化
chore: 工程维护
```

音频、chunk、ASR JSON、LLM cache、`.env` 和生成的 SRT 应放在工作目录，不能提交
进公开仓库。

## 目录结构

```text
maijev/
├── flows/maijev/
│   ├── chunker.py              # 音频抽取、静音切片
│   ├── transcriber.py          # OpenRouter MAI ASR
│   ├── merge.py                # chunk 偏移合并
│   ├── segment_llm.py          # atomize + merge LLM
│   ├── segment_prompt.md       # 通用日文断句 prompt
│   ├── translate_llm.py        # 翻译 LLM + SRT renderer
│   ├── translate_prompt.md     # 通用日译中翻译 prompt
│   ├── prepass.py              # 纯文本 pre-pass → glossary.md
│   ├── prepass_prompt.md       # pre-pass 词库生成 prompt
│   ├── frames.py               # 稀疏代表帧抽取（OCR/JEV 用）
│   ├── jev.py                  # Cloudflare Jev OCR 分类
│   ├── llm.py                  # Vertex / AI Studio / OpenRouter client
│   ├── pipeline.py             # CLI 编排入口
│   └── seg_lab.py              # 断句实验台
├── services/
├── tests/                      # 纯函数测试（不调 LLM/ffmpeg）
├── pyproject.toml
└── README.md
```
