# 进阶使用与故障排查

[返回 README](../README.md)

## 只生成确定性基线字幕

```bash
uv run python -m flows.maijev.pipeline \
  input.mp4 \
  runs/example
```

输出：

```text
runs/example/out.srt
```

## 远程来源（yt-dlp）

`input` 也可以是平台视频 ID 或完整 URL，经 yt-dlp 下载后继续同一流水线：

| 平台 | 示例 |
|---|---|
| Bilibili | `BV1ZArvBaEqL` / `https://www.bilibili.com/video/BV1ZArvBaEqL` |
| TVer | `ep12345` / `https://tver.jp/episodes/ep12345` |
| Abema | `90-979_s1_p123` / `https://abema.tv/video/episode/90-979_s1_p123` |
| YouTube | `v=dQw4w9WgXcQ` / `https://youtu.be/dQw4w9WgXcQ` |

```bash
uv run python -m flows.maijev.pipeline \
  BV1ZArvBaEqL \
  runs/example \
  --translate
```

视频下载到 `runs/example/download/`，来源信息写入 `source_meta.json`。
TVer / Abema 来源还会额外抓取出演者（cast）元数据，作为**权威人名锚点**
直接进 pre-pass（绕过 Jev 分类）——只要流水线检测到 cast 数据，即使没有
`--ocr-json` / `--prepass`，启用翻译时也会自动跑一次 pre-pass 生成词库。

## 可选：视觉上下文支线（OCR → pre-pass → 自动词库）

整条支线是可选的，而且**逐层降级**——有什么凭据就走哪一层：

```text
有 OCR 工具 + Jev 凭据（TypeSafe 或 Cloudflare）：
  ocr.json → Jev 分类筛选（丢弃效果字/对白/噪声）
           → 筛出的实体进 pre-pass

有 OCR、无 Jev 凭据：
  ocr.json → OCR 原文不筛选，直进 pre-pass

无 OCR，仅加 --prepass：
  字幕全文单独进 pre-pass

无 OCR、无 --prepass、也无来源 cast 信息：
  不调用 Jev 或 pre-pass；翻译可复用工作目录中已有的 glossary.md
```

启用词库生成时，终点是一次**纯文本 pre-pass**：Gemini 收到 OCR 文字（筛过
或原文）+ merge 产出的全部日文行，把实体变体归并成 `glossary.md` 自动词库。
它代替了逐批的内部预分析——每个翻译批次注入的是同一份词库，而不是各自从
原始 OCR 文字猜测写法。字幕合并（merge）阶段不接收 OCR 内容。

OCR 本身不在本仓库实现：`--extract-frames` 只负责抽帧，OCR 由你自己的工具
完成，结果经 `--ocr-json` 传回。

### 稀疏代表帧抽取

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
Jev 凭据（TypeSafe 或 Cloudflare）会先经 Jev 分类筛选，没有则原文直进 pre-pass。

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
# 可选：此处示例使用 Cloudflare；也可配置 TypeSafe，见 configuration.md
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

pre-pass 在翻译阶段运行：`--ocr-json`、`--prepass` 或来源 cast 信息任一存在时触发。
都没有时不新建自动词库，但会复用工作目录中已有的 `glossary.md`。


## 指定基线 SRT 路径

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

程序读取该文件并追加到 translation system prompt；人工词库与自动词库冲突时，
prompt 要求优先采用人工词库。不设置该变量时，仍会使用工作目录中已有的自动词库。
词库内容会发送给所配置的翻译模型；文件由使用者自行维护，不应提交私人词库。

## Web GUI（可选）

<a id="web-gui可选"></a>

不想敲命令行、或者视频放在另一台机器上，可以起一个本地 Web 界面：

```bash
uv sync --extra gui
uv run python -m flows.maijev.gui            # http://127.0.0.1:8792
uv run python -m flows.maijev.gui --host 0.0.0.0 --runs /vol1/maijev_runs   # 放服务器上
```

GUI 是 CLI 的薄壳：每个任务就是一个 `python -m flows.maijev.pipeline` 子进程
加一个 work_dir，进度、结果、词库全部从 work_dir 里已有的文件推导，pipeline
本身零改动。功能：

- 选本地文件（可浏览服务器目录）或粘贴 BV / TVer / Abema / YouTube 来源。
- 六阶段进度（音频 → ASR n/m → 合并 → 词库 → 翻译 → 完成）+ 实时日志。
- 下载 `out_ja_zh.srt`（中日双语）/ `out_zh.srt` / `out_llm_ja.srt` / `out.srt`；优先预览双语文件，旧任务仍支持单语预览。
- **人工词库**：右侧编辑 `glossary_user.md`（`原文 -> 写法`），「保存并重翻」
  会以 `TRANSLATE_GLOSSARY_PATH` 重跑——在输入、模型和前序配置未变时复用 ASR / 合并 / pre-pass 缓存，
  词库变化使翻译缓存失效。没有 OCR、没有 Jev 的用户就靠这一步修正译名：先开
  pre-pass 拿一版自动词库，改掉不满意的条目，重翻即可。

凭据状态在顶栏显示（只显示有无，不显示值）。绑定 `127.0.0.1` 时无鉴权；
要对外暴露请自己套一层反向代理或 SSH 隧道，服务本身没有账号体系。

## 故障排查

### `OPENROUTER_API_KEY not set`

```bash
export OPENROUTER_API_KEY=sk-or-v1-...
```

### LLM 请求失败或超时

检查 Gemini key、模型名称、网络连接和 `LLM_MAX_OUTPUT_TOKENS`，修正后使用同一
工作目录重跑，优先复用已成功的缓存。仅在确认缓存损坏或需要强制重跑时清理对应缓存。

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
