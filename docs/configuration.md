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
| `OPENROUTER_API_KEY` | 没有 Gemini key 时的 LLM fallback；同时用于 ASR |
| `GEMINI_AGENT_PLATFORM_BASE_URL` | 可选的 Vertex publisher endpoint |
| `GEMINI_MODEL` | 全局 Gemini 模型默认值（同 `--model`，默认 `gemini-2.5-pro`） |
| `SEGMENT_MODEL` | merge 阶段模型覆盖值 |
| `TRANSLATE_MODEL` | translation 阶段模型覆盖值 |
| `PREPASS_MODEL` | pre-pass（词库生成）阶段模型覆盖值 |
| `LLM_THINKING_LEVEL` | 3.x 模型的 `thinkingConfig.thinkingLevel`（`LOW`/`MEDIUM`/`HIGH`；2.x 用 `thinkingBudget`，字段不同） |
| `LLM_MAX_OUTPUT_TOKENS` | LLM 最大输出 token，默认 `65536` |
| `SEGMENT_BATCH_SIZE` | merge 每次调用的 atom 数，默认 `1000` |
| `TRANSLATE_BATCH_SIZE` | translation 每次调用的字幕行数，默认 `1000` |
| `TRANSLATE_GLOSSARY_PATH` | 可选外部术语表路径 |

模型解析顺序：`阶段_MODEL` → `GEMINI_MODEL` → 内置默认。项目主推并默认使用
`gemini-2.5-pro`。可通过 `--model` 或上述变量切换兼容型号；可用性、价格和
输出上限以所选服务商为准，修改配置后先用短片验证。

### 模型选择建议

项目以 Gemini 2.5 Pro 调整断句与翻译 prompt。更换模型时，重点验证：

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
