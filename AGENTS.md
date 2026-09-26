# maijev Agent notes

制作字幕、OCR 词库接入或校对重翻任务，请先读 [maijev-subtitles](skills/maijev-subtitles/SKILL.md)。GUI 与 CLI 共用 `flows.maijev.pipeline`；Agent 通常直接使用 CLI。

开发时在仓库根目录运行 `uv run pytest tests`。字幕时间轴由程序管理，LLM 输出按 ID 对齐；变更输出格式时保持单语产物和现有调用方兼容。

凭据在本机 `.env`，运行产物在 `runs/`；均不提交。
