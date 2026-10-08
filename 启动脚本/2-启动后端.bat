@echo off
REM ============================================================
REM  2-启动后端.bat
REM  作用：启动 FastAPI 后端（端口 8001）
REM  用法：双击运行，窗口保持开启
REM  接口文档：http://127.0.0.1:8001/docs
REM ============================================================
chcp 65001 >nul
title 后端服务 FastAPI :8001（请勿关闭）

cd /d "%~dp0..\backend"

echo ============================================================
echo   正在启动后端服务
echo   项目目录：%CD%
echo ============================================================
echo.

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 没有找到虚拟环境 .venv
    echo.
    echo        请先运行项目根目录的：scripts\setup.ps1
    echo.
    pause
    exit /b 1
)

REM 检查 MySQL 服务
sc query MySQL84 | findstr /i "RUNNING" >nul 2>&1
if not %errorlevel%==0 (
    echo [提示] MySQL84 服务未运行，正在尝试启动...
    net start MySQL84 >nul 2>&1
    if not %errorlevel%==0 (
        echo [警告] MySQL 启动失败，请用管理员身份运行本脚本
        echo.
    ) else (
        echo [信息] MySQL84 已启动
        echo.
    )
)

REM 检查 Redis（仅提示）
netstat -ano | findstr ":6379" | findstr "LISTENING" >nul 2>&1
if not %errorlevel%==0 (
    echo [提示] Redis 未运行。缓存功能会降级为直查数据库，
    echo        不影响功能，但响应会稍慢。建议先运行 1-启动Redis.bat
    echo.
)

echo [信息] 启动中，首次启动约 2-5 秒...
echo ------------------------------------------------------------
echo.

REM 注意：不要加 --reload。
REM 原因：reload 模式会产生两个进程，lifespan 执行两次，
REM       导致定时任务被注册两次、同一份数据被处理两遍。
".venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port 8001

echo.
echo ------------------------------------------------------------
echo [警告] 后端服务已停止。
echo        常见原因：MySQL 未启动 / 端口 8001 被占用
echo.
pause
