# 开发与 Prompt 实验

[返回 README](../README.md)

## Prompt 实验台

只查看 atom：

```bash
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --start 0 \
  --end 120 \
  --atoms
```

调用 merge LLM 测试 0–120 秒：

```bash
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --start 0 \
  --end 120
```

指定自定义断句 prompt 和模型：

```bash
uv run python -m flows.maijev.seg_lab \
  runs/example/asr.json \
  --system my_segment_prompt.md \
  --model gemini-2.5-pro
```

修改 prompt 或模型后，缓存键自动变化，通常无需手动清理缓存。

## GitHub 版本管理

建议流程：

```bash
git status
git diff --check
uv run pytest tests
git add <本次修改的文件>
git commit -m "fix: describe change"
git push origin master
```

commit message 使用以下前缀：

```text
feat: 新功能
fix: 修复行为
config: 配置变化
chore: 工程维护
```

音频、chunk、ASR JSON、LLM cache、`.env` 和生成的 SRT 应放在工作目录，不能提交
进公开仓库。
