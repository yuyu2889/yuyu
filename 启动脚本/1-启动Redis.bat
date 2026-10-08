@echo off
REM ============================================================
REM  1-启动Redis缓存.bat
REM  作用：启动 Redis 缓存服务
REM  用法：双击运行，窗口保持开启
REM ============================================================
chcp 65001 >nul
title Redis 缓存服务（请勿关闭）

echo ============================================================
echo   正在启动 Redis 缓存服务
echo ============================================================
echo.

netstat -ano | findstr ":6379" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [提示] 6379 端口已在监听，Redis 应该已经在运行了。
    echo.
    goto :wait
)

set "REDIS_EXE="
for /f "delims=" %%i in ('dir /s /b "%LOCALAPPDATA%\Microsoft\WinGet\Packages\redis-server.exe" 2^>nul') do (
    set "REDIS_EXE=%%i"
)

if not defined REDIS_EXE (
    echo [错误] 没有找到 redis-server.exe
    echo.
    echo        安装命令：winget install taizod1024.redis-windows-fork
    echo        或者：    启动 Redis 不是必须的，缓存会自动降级
    echo.
    pause
    exit /b 1
)

echo [信息] Redis 路径：%REDIS_EXE%
echo [信息] 正在启动，看到 "Ready to accept connections" 即表示成功
echo ------------------------------------------------------------
"%REDIS_EXE%" --port 6379

:wait
echo.
pause
