# Homeserver 部署实例

[返回 README](../README.md)

以下是维护者现有部署的记录，不是本地安装的必需步骤。

## Homeserver 自动中字队列（山川宇衣电话 / NHK）

原档仍由各 watcher 正常上传并通知；原档有真实 BV 后，新增任务至
`/vol1/maijev/jobs/`。独立 cron 每 5 分钟执行 `/vol1/maijev/run_worker.sh`，
只处理 `pending`：字幕→ASS→压制→独立 B 站中字投稿，标题 `【中字】 原标题`，简介只放 `https://github.com/Yoru0908/maijev`，全部以 `copyright=1` 自制投稿（不传转载 `--source`），**不推字幕组群**。原档版权设置不变。
`flows/maijev/styles/yamakawa_ui.tpl` 来源于用户提供的「山川宇衣」样式；
渲染依赖 `/vol1/maijev/fonts/LXGWWenKaiGB-Medium.ttf`（不会静默替换字体）。

- 任务及状态：`/vol1/maijev/jobs/{phone|nhk}-<ID>.json`；成品/SRT/ASS：`/vol1/maijev/runs/<同名任务>/`。
- 日志：`/vol1/maijev/logs/auto-publish.log`；备份：`/vol1/maijev/backups/pre-auto-20260925/`。
- `uploaded` 记录中字 BV；`failed` 不自动重试，需排查后人工改回 `pending`；
  **`review_upload` 可能已上传成功，不核实 B 站稿件之前禁止改回 `pending`。**
- 至多 100 任务、单 worker、`/vol1` 水位 85%/剩余 8GiB 停跑；所有工作/日志/缓存均在 `/vol1`。
- 直接手动试压：`TMPDIR=/vol1/maijev/tmp /vol1/maijev/venv/bin/python -m flows.maijev.burn input.mp4 out_zh.srt zh.mp4 --fontsdir /vol1/maijev/fonts`（在 `/vol1/maijev/app/` 执行，输出也必须在 `/vol1/`）。
