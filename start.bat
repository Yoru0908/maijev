@echo off
rem Windows launcher: double-click to start the maijev GUI.
chcp 65001 >nul
cd /d "%~dp0"
set URL=http://127.0.0.1:8792

where uv >nul 2>nul || (
    echo 未找到 uv。请在 PowerShell 运行：winget install astral-sh.uv
    echo 安装后重新打开本脚本。
    goto fail
)
where ffmpeg >nul 2>nul || (
    echo 未找到 ffmpeg。请在 PowerShell 运行：winget install Gyan.FFmpeg
    echo 安装后重新打开本脚本。
    goto fail
)

if not exist .env (
    copy .env.example .env >nul
    start "" notepad .env
    echo 已创建 .env，请填好 API key 并保存，然后重新双击本脚本。
    goto fail
)

rem Update only when it is a clean fast-forward; never touch local edits.
if exist .git (
    where git >nul 2>nul && (
        git pull --ff-only -q || echo （自动更新跳过：本地有改动或网络不通，继续用当前版本）
    )
)

uv sync --extra gui -q || (
    echo 依赖安装失败，见上方错误信息。
    goto fail
)

curl -s -o nul %URL%/api/env && (
    start "" %URL%
    exit /b 0
)
start "" /b powershell -NoProfile -Command "for($i=0;$i -lt 30;$i++){try{Invoke-WebRequest -UseBasicParsing %URL%/api/env -TimeoutSec 1 | Out-Null; Start-Process '%URL%'; break}catch{Start-Sleep 1}}"
echo maijev GUI 启动中，浏览器会自动打开。关闭本窗口即停止。
uv run python -m flows.maijev.gui
goto end

:fail
pause
exit /b 1
:end
pause
