# 费用记录

[返回 README](../README.md)

## 费用估算

一次 26.5 分钟综艺视频的历史运行记录（`gemini-2.5-pro`，含 pre-pass；
耗时为记录值，LLM 费用按 token 估算，不是完整账单）：

| 阶段 | 用时 | 费用 |
|---|---:|---:|
| ASR（MAI-Transcribe-2，OpenRouter） | ~30s | $0.044（精确值） |
| merge + pre-pass + translate（Gemini） | ~11min | ~$0.2（按 token 估算） |
| **合计** | **~12min** | **≈ $0.25** |

粗略换算：**一小时视频 ≈ $0.5–0.6**。LLM 部分是估算值（usage 未逐项落盘）；
实际费用随模型、服务定价、语音密度、缓存和重试次数变化。`timings.json` 记录各
阶段耗时供复盘。
