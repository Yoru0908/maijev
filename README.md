# maijev

把日语音视频整理成**可校对的字幕底稿**，支持浏览器 GUI、命令行和 Agent Skill。

名字来自微软的 **MAI ASR** 与 **Jev** 模型，项目主要搭配 **Gemini 2.5 Pro** 使用：

- **保留时间依据**：利用 MAI 的词级时间戳，由程序拆分带时间的小片段（atom），LLM 按语义分组，程序取回首尾时间。
- **参考画面写法**：可选外部 OCR + Jev 筛选人名等线索，整理共享词库，减少翻译批次间的译名差异。
- **方便反复校对**：分阶段缓存；修改词库后复用前序结果重翻，可导出日文、中文和中日双语 SRT。

**基础流程只需 MAI + LLM，OCR 和 Jev 都不是必需项。** “无需修改时间轴”仅指收音良好、语音清晰素材下的使用体验，不代表所有视频都能免调轴；文字、翻译和专有名词仍需人工校对。

[快速开始](#快速开始) · [命令行](#命令行) · [Agent Skill](#agent-skill) · [效果示例](#效果示例) · [详细文档](#详细文档)

## 快速开始

### 1. 安装

需要 Python ≥ 3.13、[uv](https://docs.astral.sh/uv/) 和 `ffmpeg` / `ffprobe`。

```bash
git clone https://github.com/Yoru0908/maijev.git
cd maijev
uv sync --extra gui
```

只使用命令行时，可以用 `uv sync` 安装基础依赖。

### 2. 配置 API

在仓库根目录创建 `.env`。下面以 OpenRouter 接入 MAI、Google AI Studio 接入 Gemini 为例：

```dotenv
OPENROUTER_API_KEY=填写你的_OpenRouter_密钥
GEMINI_API_KEY=填写你的_Gemini_密钥
GEMINI_MODEL=gemini-2.5-pro
```

`.env` 不应提交到仓库。其他 LLM 接入方式、模型覆盖和可选 Jev 凭据见 [API 与模型配置](docs/configuration.md)。

### 3. 启动 GUI

```bash
uv run python -m flows.maijev.gui
```

打开 **http://127.0.0.1:8792**：

1. 填写本地音视频路径，或 Bilibili、TVer、Abema、YouTube 的受支持链接。
2. 选择「日文合并」生成日文底稿，或「日文 + 中文」生成单语和双语字幕。
3. 点击「开始」，查看进度、日志、字幕预览并下载结果。

首次使用可以留空 **OCR JSON**。「生成自动词库（pre-pass）」仅靠字幕全文也能工作；只想先出基础底稿，可取消勾选。含出演者信息的远程来源在翻译时仍会自动整理词库。

需要修正译名时，在右侧人工词库填写 `原文 -> 写法`，点击「保存并重翻」。任务和缓存默认保存在 `runs/`；每个素材应使用独立工作目录。

GUI 的服务器模式与访问说明见 [进阶使用](docs/usage.md#web-gui可选)。

## 命令行

使用同一份 `.env`，根据需要选择一种模式：

```bash
# 日文底稿：MAI 转录 + LLM 语义合并
uv run python -m flows.maijev.pipeline "input.mp4" "runs/demo" --llm-segment

# 日文、中文和中日双语字幕；自动包含语义合并
uv run python -m flows.maijev.pipeline "input.mp4" "runs/demo" --translate

# 翻译前用全片字幕生成共享词库，不需要 OCR
uv run python -m flows.maijev.pipeline "input.mp4" "runs/demo" --translate --prepass
```

| 工作目录中的输出 | 内容 |
|---|---|
| `out.srt` | ASR 确定性断句基线 |
| `out_llm_ja.srt` | LLM 合并后的日文底稿 |
| `out_zh.srt` | 中文字幕（启用翻译时） |
| `out_ja_zh.srt` | 日文在上、中文在下的双语字幕（启用翻译时，无额外模型调用） |

远程来源、OCR JSON 格式、抽帧和自定义词库见 [进阶使用](docs/usage.md)。完整参数可运行：

```bash
uv run python -m flows.maijev.pipeline --help
```

## Agent Skill

仓库内的 Agent 可按 [maijev-subtitles Skill](skills/maijev-subtitles/SKILL.md) 调用 CLI。
需要在其他项目使用时，把 `skills/maijev-subtitles/` 整个目录放入 Agent 的技能目录，例如 Codex 的 `~/.codex/skills/`。

> 使用 $maijev-subtitles 处理这个日语视频，校对人名并导出中日双语 SRT。

Skill 是操作指引，不会自动安装 maijev 或复制 API 密钥。

## 效果示例

- [字幕视频示例 1](https://www.bilibili.com/video/BV1UHhJ6mEVC/)
- [字幕视频示例 2](https://www.bilibili.com/video/BV1sbaN66EtN/)

![待校对的烧录样例：画面人名条为「夫 啓治さん」，底部字幕仍写作「庆次」](docs/assets/demo-frame-name-card.png)

上图展示了一个需要校对的同音异字问题：人名条是「夫 啓治さん」，字幕却写成了「庆次」。
[OCR 人名对照测试](docs/ocr-name-comparison.md)复用相同转录和全片上下文：未接入 OCR 时词库写作「目黒慶次」，接入后变为「目黒啓治」，译文随之采用「啓治」。这是一次具体测试，不保证所有人名都能自动纠正，也不修改 ASR 日文原文。

## 工作原理

三种入口共用 `flows.maijev.pipeline` 和同一套任务文件：GUI 启动 CLI 子进程，Agent 也通过 CLI 执行。

```text
GUI / CLI / Agent Skill
        ↓
音视频 → MAI 词级转录 → 程序拆 atom → LLM 分组 → 程序恢复时间轴
                                                    ↓
                                              日文字幕底稿
                                                    ↓
                                     共享词库 → LLM 翻译 → 中文 / 双语 SRT
```

atom 是带时间的小片段，可以包含多个词。LLM 返回分组或翻译文本，程序校验 ID、恢复时间并输出字幕；LLM 不生成最终时间戳。

可选词库支线接收外部 OCR：有 Jev 凭据时先筛选线索，没有时使用 OCR 原文；也可以只用字幕全文生成词库。OCR 本身由外部工具完成，`--extract-frames` 仅负责抽帧。更多实现见 [流水线与缓存](docs/architecture.md)。

## 费用

费用由 MAI 转录和各 LLM 阶段共同构成，受素材、模型、缓存和重试次数影响。
历史记录中，26.5 分钟素材使用 Gemini 2.5 Pro，ASR 为 $0.044，LLM 约 $0.2（估算，未逐项记录完整账单）；详见 [费用记录](docs/costs.md)。

## 详细文档

| 想了解什么 | 文档 |
|---|---|
| API 后端、模型与环境变量 | [API 与模型配置](docs/configuration.md) |
| 远程视频、OCR、词库、GUI 服务器模式、故障排查 | [进阶使用](docs/usage.md) |
| atom、LLM 契约、缓存与目录结构 | [流水线实现与缓存](docs/architecture.md) |
| 修改 prompt、运行测试和提交代码 | [开发与 Prompt 实验](docs/development.md) |
| 项目图文介绍 | [阅读版](docs/overview.html) |
