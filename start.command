#!/bin/bash
# macOS launcher: double-click in Finder to start the maijev GUI.
cd "$(dirname "$0")" || exit 1
URL=http://127.0.0.1:8792

fail() { echo; echo "$1"; read -r -p "按回车关闭…"; exit 1; }

command -v uv >/dev/null || fail "未找到 uv。请先在终端运行：curl -LsSf https://astral.sh/uv/install.sh | sh"
command -v ffmpeg >/dev/null || fail "未找到 ffmpeg。请先在终端运行：brew install ffmpeg"

if [ ! -f .env ]; then
    cp .env.example .env
    open -e .env
    fail "已创建 .env，请填好 API key 并保存，然后重新双击本脚本。"
fi

# Update only when it is a clean fast-forward; never touch local edits.
if [ -d .git ] && command -v git >/dev/null; then
    git pull --ff-only -q || echo "（自动更新跳过：本地有改动或网络不通，继续用当前版本）"
fi

uv sync --extra gui -q || fail "依赖安装失败，见上方错误信息。"

if curl -s -o /dev/null "$URL/api/env"; then
    open "$URL"
    exit 0
fi
(for _ in $(seq 30); do curl -s -o /dev/null "$URL/api/env" && exec open "$URL"; sleep 1; done) &
echo "maijev GUI 启动中，浏览器会自动打开。关闭本窗口即停止。"
exec uv run python -m flows.maijev.gui
