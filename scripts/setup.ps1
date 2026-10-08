# ============================================================
#  V2 环境搭建与启动脚本（Windows PowerShell）
# ============================================================
#  用途：在一台新电脑上从零把 V2 跑起来。
#  用法：以管理员身份打开 PowerShell，执行：
#         cd C:\code\LabBookingSystemV2
#         .\scripts\setup.ps1
# ============================================================

$ErrorActionPreference = 'Stop'

Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  高校实验室设备预约管理系统 V2 - 环境搭建" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootDir = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $RootDir "backend"
$FrontendDir = Join-Path $RootDir "frontend"

# ---------------------------------------------------------------------------
#  第 1 步：检查必要软件
# ---------------------------------------------------------------------------
Write-Host "[1/6] 检查必要软件..." -ForegroundColor Yellow

$pythonExe = "C:\Program Files\Python312\python.exe"
if (-not (Test-Path $pythonExe)) {
    # 兼容其他安装位置
    $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
}
if (-not $pythonExe) {
    Write-Host "  [错误] 没有找到 Python。请先安装 Python 3.12：" -ForegroundColor Red
    Write-Host "         winget install Python.Python.3.12" -ForegroundColor White
    exit 1
}
Write-Host "  [OK] Python: $pythonExe" -ForegroundColor Green

$nodeExe = (Get-Command node -ErrorAction SilentlyContinue).Source
if (-not $nodeExe) {
    Write-Host "  [错误] 没有找到 Node.js。请先安装：" -ForegroundColor Red
    Write-Host "         winget install OpenJS.NodeJS.LTS" -ForegroundColor White
    exit 1
}
Write-Host "  [OK] Node.js: $(node --version)" -ForegroundColor Green

# 检查 MySQL 服务
$mysqlService = Get-Service -Name "MySQL84" -ErrorAction SilentlyContinue
if (-not $mysqlService) {
    $mysqlService = Get-Service | Where-Object { $_.Name -match 'mysql' } | Select-Object -First 1
}
if (-not $mysqlService) {
    Write-Host "  [警告] 没有找到 MySQL 服务。请先安装 MySQL 8.4：" -ForegroundColor Yellow
    Write-Host "         参考 docs/环境搭建说明.md" -ForegroundColor White
} elseif ($mysqlService.Status -ne 'Running') {
    Write-Host "  [提示] MySQL 服务未运行，正在启动..." -ForegroundColor Yellow
    Start-Service $mysqlService.Name
} else {
    Write-Host "  [OK] MySQL 服务运行中：$($mysqlService.Name)" -ForegroundColor Green
}

# 检查 Redis
$redisRunning = $false
try {
    $tcp = New-Object System.Net.Sockets.TcpClient
    $tcp.Connect("127.0.0.1", 6379)
    $redisRunning = $tcp.Connected
    $tcp.Close()
} catch { }
if ($redisRunning) {
    Write-Host "  [OK] Redis 运行中（6379）" -ForegroundColor Green
} else {
    Write-Host "  [提示] Redis 未运行。缓存功能会降级，但系统仍可用。" -ForegroundColor Yellow
    Write-Host "         建议启动 Redis 以获得完整功能" -ForegroundColor White
}

Write-Host ""

# ---------------------------------------------------------------------------
#  第 2 步：准备后端配置
# ---------------------------------------------------------------------------
Write-Host "[2/6] 准备后端配置..." -ForegroundColor Yellow

$envFile = Join-Path $BackendDir ".env"
$envExample = Join-Path $BackendDir ".env.example"

if (-not (Test-Path $envFile)) {
    Copy-Item $envExample $envFile
    Write-Host "  [创建] 已从 .env.example 生成 .env" -ForegroundColor Green
    Write-Host "  [重要] 请编辑 backend\.env 填写数据库密码！" -ForegroundColor Red
    Write-Host ""
    Write-Host "         需要修改的项：" -ForegroundColor White
    Write-Host "           DB_PASSWORD  - 你的 MySQL 密码" -ForegroundColor White
    Write-Host "           SECRET_KEY   - 随机密钥，生成命令：" -ForegroundColor White
    Write-Host '             python -c "import secrets; print(secrets.token_urlsafe(48))"' -ForegroundColor Gray
    Write-Host ""
    Read-Host "  填写完成后按回车继续（或按 Ctrl+C 退出）"
} else {
    Write-Host "  [OK] .env 已存在" -ForegroundColor Green
}

Write-Host ""

# ---------------------------------------------------------------------------
#  第 3 步：创建虚拟环境并安装后端依赖
# ---------------------------------------------------------------------------
Write-Host "[3/6] 安装后端依赖..." -ForegroundColor Yellow

$venvPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "  正在创建虚拟环境..." -ForegroundColor Gray
    & $pythonExe -m venv (Join-Path $BackendDir ".venv")
}

& $venvPython -m pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple 2>&1 | Out-Null
& $venvPython -m pip config set global.trusted-host pypi.tuna.tsinghua.edu.cn 2>&1 | Out-Null

Write-Host "  正在安装依赖（使用清华镜像，约 1-2 分钟）..." -ForegroundColor Gray
& $venvPython -m pip install -r (Join-Path $BackendDir "requirements.txt") --quiet

if ($LASTEXITCODE -ne 0) {
    Write-Host "  [错误] 依赖安装失败" -ForegroundColor Red
    exit 1
}
Write-Host "  [OK] 后端依赖安装完成" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
#  第 4 步：初始化数据库
# ---------------------------------------------------------------------------
Write-Host "[4/6] 初始化数据库..." -ForegroundColor Yellow
Write-Host "  这一步会创建数据表并写入示例数据" -ForegroundColor Gray

Push-Location $BackendDir
try {
    & $venvPython "scripts\init_db.py"
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  [错误] 数据库初始化失败。请检查 .env 里的数据库配置" -ForegroundColor Red
        exit 1
    }
} finally {
    Pop-Location
}
Write-Host "  [OK] 数据库初始化完成" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
#  第 5 步：安装前端依赖
# ---------------------------------------------------------------------------
Write-Host "[5/6] 安装前端依赖..." -ForegroundColor Yellow

Push-Location $FrontendDir
try {
    if (-not (Test-Path "node_modules")) {
        Write-Host "  正在安装（使用 npmmirror 镜像，约 1-2 分钟）..." -ForegroundColor Gray
        npm install --registry=https://registry.npmmirror.com
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  [错误] 前端依赖安装失败" -ForegroundColor Red
            exit 1
        }
    } else {
        Write-Host "  [OK] node_modules 已存在，跳过安装" -ForegroundColor Green
    }
} finally {
    Pop-Location
}
Write-Host "  [OK] 前端依赖就绪" -ForegroundColor Green
Write-Host ""

# ---------------------------------------------------------------------------
#  第 6 步：完成
# ---------------------------------------------------------------------------
Write-Host "[6/6] 搭建完成！" -ForegroundColor Green
Write-Host ""
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host "  接下来怎么启动" -ForegroundColor Cyan
Write-Host "============================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "  方式一（推荐）：双击 启动脚本\ 目录下的三个 bat 文件" -ForegroundColor White
Write-Host "    1-启动Redis.bat" -ForegroundColor Gray
Write-Host "    2-启动后端.bat" -ForegroundColor Gray
Write-Host "    3-启动前端.bat" -ForegroundColor Gray
Write-Host ""
Write-Host "  方式二：手动执行" -ForegroundColor White
Write-Host "    # 终端 1 - 后端" -ForegroundColor Gray
Write-Host "    cd backend" -ForegroundColor Gray
Write-Host "    .venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8001" -ForegroundColor Gray
Write-Host ""
Write-Host "    # 终端 2 - 前端" -ForegroundColor Gray
Write-Host "    cd frontend" -ForegroundColor Gray
Write-Host "    npm run dev" -ForegroundColor Gray
Write-Host ""
Write-Host "  然后浏览器打开：http://127.0.0.1:5173" -ForegroundColor Yellow
Write-Host ""
Write-Host "  演示账号：" -ForegroundColor White
Write-Host "    管理员  admin     / Admin@123" -ForegroundColor Gray
Write-Host "    教师    teacher1  / Teacher@123" -ForegroundColor Gray
Write-Host "    学生    student1  / Student@123" -ForegroundColor Gray
Write-Host ""
Write-Host "  接口文档：http://127.0.0.1:8001/docs" -ForegroundColor White
Write-Host ""
