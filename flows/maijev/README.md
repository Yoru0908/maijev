# flows.maijev

`flows.maijev` 是 `maijev` 的核心字幕流水线：

```text
MAI-Transcribe-2 ASR → atomize → LLM merge → translation → SRT
```

## 运行

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
export GEMINI_AGENT_PLATFORM_API_KEY=AQ...

uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example \
  --translate
```

参数：

```text
input       视频或音频
work_dir    工作目录和输出目录
--srt       确定性基线 SRT 的可选路径
--llm-segment 运行 LLM 合并
--translate    运行中文翻译，同时自动启用 LLM 合并
```

## 输出

```text
work_dir/
├── audio.wav
├── chunks/
│   ├── manifest.json
│   ├── chunk_XX.wav
│   └── chunk_XX.wav.json
├── asr.json
├── out.srt
├── merge_cache/
├── out_llm_ja.srt
├── zh_cache/
├── out_zh.srt
├── prepass_cache/          # --ocr-json / --prepass 时存在
├── glossary.md             # pre-pass 产出的自动词库
└── timings.json
```

所有请求都尽量使用缓存，缓存键包含输入内容、system prompt 和 model——
prompt、模型或词库变更会自动失效，不需要手动删除。ASR chunk 缓存可以
继续保留。

## 模块

| 文件 | 职责 |
|---|---|
| `chunker.py` | ffmpeg 抽音频、检测静音、生成 chunk |
| `transcriber.py` | OpenRouter MAI ASR、word 时间戳、diarization、可选 phrase list |
| `merge.py` | 片间偏移合并，生成统一 ASR payload |
| `segment_llm.py` | 词级 atomize、短促相槌预处理、LLM 合并 |
| `segment_prompt.md` | 通用日文字幕断句 prompt |
| `translate_llm.py` | JSON 翻译、后处理、时间轴恢复、SRT renderer |
| `translate_prompt.md` | 通用日译中翻译 prompt（无领域词库） |
| `frames.py` | 稀疏代表帧抽取（OCR/JEV 上下文用） |
| `jev.py` | Cloudflare Jev OCR 分类 |
| `prepass.py` | 纯文本 pre-pass：OCR anchor + 日文行 → glossary.md |
| `prepass_prompt.md` | pre-pass 词库生成 prompt |
| `seg_lab.py` | atom 和 merge prompt 实验台 |
| `llm.py` | Vertex、AI Studio、OpenRouter LLM client |
| `pipeline.py` | CLI 编排入口 |

## 两阶段断句

### 拆：确定性 atomize

`build_atoms()` 根据 word-level 时间戳处理：

- speaker 切换
- 词间静音至少 1 秒
- 硬标点和软标点
- 48 字长度兜底

普通 `うん` 在送入 merge LLM 前处理：同 speaker 并入邻句，跨 speaker 相槌并入
最近邻，近距离的不同 speaker `うん / うん` 保持独立。

### 合：LLM merge

LLM 输入：

```text
id | speaker | gap | text
```

LLM 输出：

```json
{"groups": [[id, id], [id, id, id]]}
```

模型只决定相邻 atom 的分组。时间戳由程序根据 group 首尾 atom 恢复，模型不接触
最终 SRT 时间轴。

当前默认值：

```text
1000 atoms / batch
最多 2 个 batch 并行
merge 失败重试后直接报错
```

## 翻译契约

翻译 LLM 输入：

```text
id<TAB>日文字幕
```

翻译 LLM 输出：

```json
{
  "lines": [
    {"id": 0, "zh": "中文译文"}
  ]
}
```

程序按 id 对回原始 `MergedLine` 的 `start`、`end`，再生成 `out_zh.srt`。时间戳不
发送给翻译模型，也不由模型生成。

翻译默认每批 1000 行，最大输出 token 默认 65000，可通过
`LLM_MAX_OUTPUT_TOKENS` 覆盖。术语一致性有两个来源，注入到同一个【术语表】
段落：`TRANSLATE_GLOSSARY_PATH` 指向仓库外的人工术语表（冲突时优先），以及
`glossary.md`——`--ocr-json` 或 `--prepass` 时由 `prepass.py` 对全片做一次
纯文本 pre-pass 生成的自动词库。仓库本身不提供领域词库。

## 实验台

```bash
# 只查看 atom
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json --start 0 --end 120 --atoms

# 调用 merge LLM
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json --start 0 --end 120

# 使用自定义断句 prompt
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --system my_segment_prompt.md \
  --model gemini-2.5-pro
```

缓存键已包含 system prompt 和 model，修改 prompt 后旧缓存自动失效，可直接
重跑正式 pipeline。
