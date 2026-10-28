# maijev Agent notes

制作字幕、OCR 词库接入或校对重翻任务，请先读 [maijev-subtitles](skills/maijev-subtitles/SKILL.md)。GUI 与 CLI 共用 `flows.maijev.pipeline`；Agent 通常直接使用 CLI。

开发时在仓库根目录运行 `uv run pytest tests`。字幕时间轴由程序管理，LLM 输出按 ID 对齐；变更输出格式时保持单语产物和现有调用方兼容。

凭据在本机 `.env`，运行产物在 `runs/`；均不提交。

用户能感觉到的改动（新选项、输出变化、默认值、修复）要在 `CHANGELOG.md` 顶部记一条，并同步提高 `pyproject.toml` 和 `uv.lock` 里的版本号；`tests/test_version.py` 检查 CHANGELOG 第一节就是当前版本。GUI 的版本号和更新内容都从这两处读取。

项目主要使用 Gemini 3.8 Flash。临时测试环境中的模型覆盖不代表主推配置；用户明确指定其他模型时再切换，记录实测采用的具体型号。
