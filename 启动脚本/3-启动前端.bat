@echo off
REM ============================================================
REM  3-启动前端.bat
REM  作用：启动 Vue3 前端开发服务器
REM  用法：双击运行，窗口保持开启
REM  访问地址：http://127.0.0.1:5173
REM ============================================================
chcp 65001 >nul
title 前端服务 Vue3 :5173（请勿关闭）

cd /d "%~dp0..\frontend"

echo ============================================================
echo   正在启动前端服务
echo   项目目录：%CD%
echo ============================================================
echo.

if not exist "node_modules" (
    echo [提示] 没有找到 node_modules，正在自动安装依赖...
    echo        首次安装约 1-2 分钟，请耐心等待
    echo.
    call npm install --registry=https://registry.npmmirror.com
    if not %errorlevel%==0 (
        echo.
        echo [错误] 前端依赖安装失败，请检查网络后重试
        pause
        exit /b 1
    )
)

REM 检查后端是否已启动
netstat -ano | findstr ":8001" | findstr "LISTENING" >nul 2>&1
if not %errorlevel%==0 (
    echo [提示] 后端服务（8001）未运行。
    echo        页面能打开，但登录、设备列表等数据会加载失败。
    echo        请先运行 2-启动后端.bat
    echo.
)

REM 检查 5173 端口是否被占用（常见于旧项目还在跑）
netstat -ano | findstr ":5173" | findstr "LISTENING" >nul 2>&1
if %errorlevel%==0 (
    echo [警告] 5173 端口已被占用！
    echo.
    echo        可能原因：旧的开发服务器还在运行。
    echo        解决办法：找到占用进程并结束它：
    echo            netstat -ano ^| findstr :5173
    echo            taskkill /F /PID ^<进程号^>
    echo.
    echo        或者修改 vite.config.js 里的 server.port 换个端口。
    echo.
    pause
)

echo [信息] 启动中...
echo        启动成功后，浏览器打开：http://127.0.0.1:5173
echo ------------------------------------------------------------
echo.

call npm run dev

echo.
echo ------------------------------------------------------------
echo [警告] 前端服务已停止。
echo.
pause
