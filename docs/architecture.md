# 流水线实现与缓存

[返回 README](../README.md)

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
├── download/                 # yt-dlp 下载的远程视频（远程来源时）
├── source_meta.json          # 来源平台 / ID / URL / cast 名单（远程来源时）
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
├── out_ja_zh.srt             # 日文在上、中文在下的双语字幕
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

修改人工词库后，翻译缓存自动失效，使用原命令重跑即可。仅需强制重新翻译时：

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
文字、噪声或未知。分类响应按完整请求体 hash 缓存到 `jev_cache/`。Jev 后端
二选一：官方 TypeSafe API（`TYPESAFE_API_KEY`）或 Cloudflare Workers AI
（`CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`）。

随后 `prepass.run_prepass()` 把 anchor 与 merge 产出的全部日文行一起发给
Gemini 做**一次共享的纯文本调用**，产出 `glossary.md` 自动词库
（`原文 -> 写法`，与 `TRANSLATE_GLOSSARY_PATH` 同格式）。anchor 来源可以是：

- Jev 筛出的 OCR 实体（有凭据时）；
- 未筛选的 OCR 原文（无 Jev 凭据时）；
- 远程来源的 cast 名单（TVer/Abema，权威人名，绕过 Jev 直进）。

这个词库——而不是原始 OCR 文字——注入每个翻译批次，以减少全片译名漂移，仍需人工校对。

字幕合并阶段不接收 OCR 内容。没有 `--ocr-json`、没有 cast 数据也没有
`--prepass` 时，Jev 和 pre-pass 都不会被调用。


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
BATCH_SIZE = 1000 atoms   （SEGMENT_BATCH_SIZE 可调）
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
BATCH_SIZE = 1000 lines   （TRANSLATE_BATCH_SIZE 可调）
MAX_WORKERS = 2
maxOutputTokens = 65536
```

确定性后处理包括：

- 校验每个输入 id 是否都有返回。
- 清理多余标点和格式。
- 保留 ` -` speaker 分隔符。
- 空译文回退到对应原文，避免静默丢行。

## 当前限制

- speaker 编号只保证在单个 ASR 请求范围内有效；跨 chunk 不保证同一个人继续使用
  同一个编号。严格的全片 speaker identity 需要额外的全局 diarization 或 speaker
  embedding。
- LLM 的响应速度和最大输出长度取决于具体模型和后端；`65536` 是配置上限，不
  代表每次调用一定生成这么多 token。
- phrase list 需要调用方按领域自行传入，不由仓库维护。

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
│   ├── jev.py                  # TypeSafe / Cloudflare Jev OCR 分类
│   ├── llm.py                  # Vertex / AI Studio / OpenRouter client
│   ├── source.py               # yt-dlp 远程来源 + TVer/Abema cast
│   ├── pipeline.py             # CLI 编排入口
│   ├── seg_lab.py              # 断句实验台
│   └── gui/                    # 可选 Web GUI（FastAPI + 单页）
│       ├── server.py           #   子进程 + SSE + work_dir 文件暴露
│       └── static/             #   index.html / app.js
├── docs/                       # 使用指南与实现说明
├── services/
├── tests/                      # 纯函数测试（不调 LLM/ffmpeg）
├── pyproject.toml
└── README.md
```
