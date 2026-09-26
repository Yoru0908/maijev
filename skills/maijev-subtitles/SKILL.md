---
name: maijev-subtitles
description: 使用 maijev 的命令行或 GUI 为日语视频制作字幕，接入外部 OCR 整理人名词库、校对重翻并导出日文、中文和中日双语 SRT。适用于本地音视频或受支持的视频链接，不负责默认发布或上传成片。
---

# maijev 字幕制作

## 定位仓库与执行入口

从用户指定的位置或当前工作区找到含 `pyproject.toml` 和 `flows/maijev/pipeline.py` 的 maijev 仓库。Skill 安装目录不一定是代码目录。下面的命令均在仓库根目录执行，输入和输出路径用实际路径替换并加引号。

- Agent 执行通常用 CLI；用户想自己查看进度、改词库时使用 GUI。GUI 是同一条 CLI 流水线的薄壳，不是另一套处理实现。
- 用 `uv run python -m flows.maijev.pipeline --help` 核对当前参数。依赖未装时按根目录 README 安装；GUI 需要 `uv sync --extra gui`。
- 根目录 `.env` 配置 ASR 和 Gemini 凭据。检查所需变量是否已设置即可，不输出密钥，不把 `.env` 打包或提交。优先保留用户已选模型；需要覆盖时传 `--model`，注意各阶段环境变量的优先级更高。
- 一个输入素材对应一个独立 `work_dir`。复用缓存时保留原目录和同一素材，不用另一个视频覆盖它。

## 普通字幕任务

```sh
uv run python -m flows.maijev.pipeline "/path/to/input.mp4" "/path/to/runs/example" --translate --prepass
```

也接受 README 支持的 Bilibili、TVer、Abema、YouTube ID 或 URL。缺失的本地路径会尝试作为远程来源解析，因此先确认本地文件存在。

`--translate` 包含语义合并和中文翻译；`--prepass` 在没有 OCR 时用全部日文行生成自动词库。仅需日文时用 `--llm-segment`；只需原始 ASR 时不加这两个参数。

默认产物：

| 文件 | 用途 |
|---|---|
| `out.srt` | 原始 ASR 确定性断句基线 |
| `out_llm_ja.srt` | 合并断句后的日文 |
| `out_zh.srt` | 中文字幕；CLI 返回路径仍为此文件 |
| `out_ja_zh.srt` | 双语 SRT：日文在上、中文在下，同一序号与时间轴 |
| `glossary.md` | 自动词库（启用 pre-pass / OCR / cast 时） |
| `timings.json` | 本次各阶段耗时；缓存运行耗时不代表首次处理速度 |

双语导出由程序组装，不额外调用模型。旧任务使用原参数重跑即可复用缓存补齐文件。SRT 不写 ASS 的 `\fad` 等特效；如用户要 ASS 或烧录，另按 README 中 `burn` 的接口处理。

## 画面文字 → 共享词库

人名、地名在声音里可能存在同音异字，画面名条可以提供汉字依据。先看素材是否确实包含有用文字，选择出现名条或地点标识的代表帧，并保留解释人物关系所需的上下文。

目前 maijev 只抽帧和接收 OCR JSON，**不内置 OCR 引擎**。使用用户现有或本机可用的 OCR 工具读取文字，再传回流水线；不要把手动写出的文本称为机器 OCR 结果。

```sh
# 只转录并准备代表帧，避免为了准备 OCR 先付费翻译一次
uv run python -m flows.maijev.pipeline "/path/to/input.mp4" "/path/to/runs/example" --extract-frames

# 外部工具生成 OCR JSON 后，继续同一任务
uv run python -m flows.maijev.pipeline "/path/to/input.mp4" "/path/to/runs/example" --translate --ocr-json "/path/to/ocr.json"
```

最小 JSON 是 observation 列表，ID 唯一，`text` 非空：

```json
[{"id":"frame-001-name","text":"画面实际读出的姓名","start":12.3,"end":14.8,"score":0.9}]
```

时间以本次输入视频的秒数为准，`score`、时间和 `bbox` 可省略。剪裁视频后要换算时间；不要混用原片和短片的坐标。

实际调用链：

```text
外部 OCR → Jev 语义分类 → 人名等线索 + 全部合并日文行
                                   ↓
                          Gemini pre-pass
                                   ↓
                             glossary.md
                                   ↓
                          每个翻译批次共用
```

无 Jev 凭据时，OCR 原文直进 pre-pass；无 OCR 时，`--prepass` 仍可只用字幕全文。ASR 和字幕合并不接收 OCR，画面线索主要影响词库与译文，**不会自动纠正双语字幕中的日文原文**。

OCR 和分类都可能出错。检查 `ocr_classification.json`、`ocr_context.txt`、`glossary.md` 与 pre-pass 缓存中的 `review` 项。尤其核对姓名是否真的属于同一个人；分类置信度不能代替 OCR 文字准确度。做效果对照时保持 ASR/合并文本和模型一致，明确词库使用的上下文范围。

## 人工校对与重翻

人工确认词库写到独立文件，格式为 `原文 -> 写法`，不要直接编辑下一次会被覆盖的 `glossary.md`。

```sh
TRANSLATE_GLOSSARY_PATH="/path/to/runs/example/glossary_user.md" \
  uv run python -m flows.maijev.pipeline "/path/to/input.mp4" "/path/to/runs/example" --translate --prepass
```

若原任务用了 `--ocr-json`、模型覆盖或其他选项，重跑时继续传入。人工词库优先于自动词库。ASR、合并、pre-pass 在输入与配置未变时命中缓存；修改词库使翻译缓存失效，因此只需重翻受影响的内容。不要删整个任务目录来“刷新”。

对无法确定的人名、听不清的语句，列出证据和待校对点，不凭空补齐。人工修正日文后要同步检查双语对应关系，避免只改一个文件留下不一致结果。

## GUI

```sh
uv run python -m flows.maijev.gui --host 127.0.0.1 --port 8792 --runs "/path/to/runs"
```

选择“日文 + 中文”会同时生成双语 SRT。任务完成后可预览、下载“中日双语 SRT”，并保留单语下载。修改“人工词库”后点“保存并重翻”。GUI 无鉴权，按用户需求在本机启动即可。

## 交付检查

确认任务退出状态和文件内容，而不只看文件存在（失败重跑可能留下旧文件）。核对字幕数量、序号、起止时间、双语语言顺序；抽查人名与画面依据，标明仍需校对的部分。交付实际文件路径和简短运行说明。发现模型服务失败先检查已保存的日志与缓存，不反复重跑整片。

字幕制作不意味着用户要求发布成片；`auto_publish`、上传队列和远程部署只在另有明确任务时使用。
