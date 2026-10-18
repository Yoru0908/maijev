# API 与模型配置

[返回 README](../README.md)

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
| `ANTHROPIC_API_KEY` | Claude 原生 API（`ANTHROPIC_BASE_URL` 可改为 Anthropic 格式的中转） |
| `OPENAI_API_KEY` | GPT 原生 API（`OPENAI_BASE_URL` 可改地址，默认 `https://api.openai.com/v1`） |
| `LLM_RELAY_BASE_URL` + `LLM_RELAY_API_KEY` | OpenAI 兼容中转站，任何模型都可走，例如 `https://relay.example/v1` |
| `OPENROUTER_API_KEY` | 最后的 LLM fallback；同时用于 ASR |
| `GEMINI_AGENT_PLATFORM_BASE_URL` | 可选的 Vertex publisher endpoint |
| `GEMINI_MODEL` | 全局模型默认值（同 `--model`，默认 `gemini-3.8-flash`） |
| `SEGMENT_MODEL` | merge 阶段模型覆盖值 |
| `TRANSLATE_MODEL` | translation 阶段模型覆盖值 |
| `PREPASS_MODEL` | pre-pass（词库生成）阶段模型覆盖值 |
| `LLM_THINKING_LEVEL` | 3.x 模型的 `thinkingConfig.thinkingLevel`（`LOW`/`MEDIUM`/`HIGH`；2.x 用 `thinkingBudget`，字段不同） |
| `LLM_MAX_OUTPUT_TOKENS` | LLM 最大输出 token，默认 `65536` |
| `SEGMENT_BATCH_SIZE` | merge 每次调用的 atom 数，默认 `1000` |
| `TRANSLATE_BATCH_SIZE` | translation 每次调用的字幕行数，默认 `1000` |
| `TRANSLATE_GLOSSARY_PATH` | 可选外部术语表路径 |

模型解析顺序：`阶段_MODEL` → `GEMINI_MODEL` → 内置默认。合并和翻译可以用不同模型：
`.env` 里分别设 `SEGMENT_MODEL` / `TRANSLATE_MODEL`，或单次运行时用 CLI 的
`--segment-model` / `--translate-model`、GUI 的「合并模型」「翻译模型」；后两者会覆盖
`.env` 的阶段设置。pre-pass 未单独设置 `PREPASS_MODEL` 时跟随翻译模型。项目主推并默认使用
`gemini-3.8-flash`。只允许通过 `--model` 或上述变量选择 `gemini-3.8-flash`、`gemini-4-argon` 或 `gemini-3.1-pro-preview`；其他值在下载/听写前报错。可用性、价格和
输出上限以所选服务商为准，修改配置后先用短片验证。

`gemini-4-argon` 只在 Vertex 的 global location 提供，默认端点会返回 404。
使用 Argon 时把 `GEMINI_AGENT_PLATFORM_BASE_URL` 设为带项目的 global 地址
（3.8 Flash 在该地址同样可用）：

```bash
export GEMINI_AGENT_PLATFORM_BASE_URL=https://aiplatform.googleapis.com/v1beta1/projects/<项目ID或编号>/locations/global/publishers/google/models
```

### Claude / GPT（未测试，效果自负）

除 Gemini 外还允许 `claude-sonnet-5-5`、`claude-opus-5-5`、`claude-fable-5-1`、`gpt-6.1-sol`。
**推荐仍是 Gemini**：prompt、批次大小和超时都按 Gemini 3.8 Flash 调过，其他模型只确认过
能返回可解析的 JSON，整片质量、速度和费用没有验证。

每个模型按下面顺序选第一个配置好的后端：

1. 原生 API：Gemini 用 Vertex / AI Studio key，Claude 用 `ANTHROPIC_API_KEY`，GPT 用 `OPENAI_API_KEY`
2. 中转站：`LLM_RELAY_BASE_URL` + `LLM_RELAY_API_KEY`（OpenAI chat/completions 格式，模型名用上面的原生 ID）
3. OpenRouter：`OPENROUTER_API_KEY`（自动换成 `anthropic/claude-sonnet-5.5` 这类 ID）

ASR 本来就要 `OPENROUTER_API_KEY`，所以不配其他 key 时 Claude / GPT 直接走 OpenRouter。
`LLM_THINKING_LEVEL` 只对 Gemini 生效。一个模型找不到任何后端时，在下载 / 听写前报错。

想用 ChatGPT / Claude 订阅登录的反代（如 CLIProxyAPI）的，自己架好后填成中转站即可；
这类用法可能违反服务商条款，项目不内置。

### 模型选择建议

断句与翻译 prompt 以 Gemini 3.8 Flash 验证。更换模型时，重点验证：

- 合并关系是否连续、不重叠；翻译是否为每个输入 id 返回一次结果。
- 人名汉字、敬称和语气的处理是否符合需求。
- 输出上限能否容纳当前批次；漏行、截断引起的重试也会增加费用。

`SEGMENT_BATCH_SIZE` / `TRANSLATE_BATCH_SIZE` 控制批次大小，默认各 1000。
共享词库帮助减少跨批次译名差异，不代表译名一定正确或完全一致。

非 Gemini 模型（OpenAI / Claude 等）不单独接端点：只配 `OPENROUTER_API_KEY`
时 LLM 走 OpenRouter chat/completions，`GEMINI_MODEL=anthropic/claude-…`
这类 OpenRouter 模型名即可切换。prompt 与 JSON 契约按 Gemini 调过，换家
需自行验证遵循度。

Jev OCR 上下文（可选；仅 `--ocr-json` 时用到），两个后端任选其一：

| 环境变量 | 用途 |
|---|---|
| `TYPESAFE_API_KEY` | 官方 TypeSafe API（`api.typesafe.ai`，推荐） |
| `CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN` | Cloudflare Workers AI 上的 Jev |
| `JEV_BACKEND` | 两套凭据都在时强制选择：`typesafe` 或 `cloudflare` |

不设 `JEV_BACKEND` 时优先官方 API，缺省回落 Cloudflare。两组凭据都没有时
`--ocr-json` 仍可用：跳过 JEV 分类，OCR 原始文字直接进 pre-pass。同理
`--prepass` 可在没有任何 OCR 时单独运行。

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
