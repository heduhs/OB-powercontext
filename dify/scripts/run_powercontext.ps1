# 一键启动 PowerContext Server(Windows PowerShell,无需 Docker)
# 用法:  .\scripts\run_powercontext.ps1              # 首次会自动建虚拟环境并安装
#        .\scripts\run_powercontext.ps1 -Port 8100    # 指定端口
param(
    [int]$Port = 8100,
    [string]$DataDir = "",
    [switch]$RegenerateToken
)

$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
$Venv = Join-Path $Root ".venv-powercontext"
$TokenFile = Join-Path $Root "PC_TOKEN.txt"
if (-not $DataDir) { $DataDir = Join-Path $Root ".pc-data" }

if (-not (Test-Path (Join-Path $Venv "Scripts\powercontext.exe"))) {
    Write-Host "[1/3] 创建虚拟环境并安装 powercontext[cli,server](约 2-3 分钟)..." -ForegroundColor Cyan
    python -m venv $Venv
    & (Join-Path $Venv "Scripts\python.exe") -m pip install -q --upgrade pip
    # 直接从官方仓库安装;已 clone 过 powercontext 的话可改用 pip install -e "<repo>\[cli,server]"
    & (Join-Path $Venv "Scripts\python.exe") -m pip install -q "powercontext[cli,server] @ git+https://github.com/oceanbase/powercontext.git@master"
} else {
    Write-Host "[1/3] 复用已有虚拟环境 $Venv" -ForegroundColor DarkCyan
}

if ($RegenerateToken -or -not (Test-Path $TokenFile)) {
    $tok = (& (Join-Path $Venv "Scripts\python.exe") -c "import secrets;print(secrets.token_urlsafe(24))").Trim()
    Set-Content -Path $TokenFile -Value $tok -NoNewline -Encoding ascii
    Write-Host "[2/3] 已生成新的访问令牌 -> $TokenFile" -ForegroundColor Cyan
} else {
    Write-Host "[2/3] 复用已有令牌 $TokenFile" -ForegroundColor DarkCyan
}
$TOKEN = (Get-Content $TokenFile -Raw).Trim()

$env:POWERCONTEXT_HOME = $DataDir
$env:POWERCONTEXT_SERVER_HTTP_HOST = "127.0.0.1"
$env:POWERCONTEXT_SERVER_HTTP_PORT = "$Port"
$env:POWERCONTEXT_SERVER_DATABASE_KIND = "sqlite"     # 可换 seekdb / oceanbase
$env:POWERCONTEXT_SERVER_ACCESS_MODE = "enforced"     # 开启 Bearer 鉴权
$env:POWERCONTEXT_SERVER_AUTH_TOKEN = $TOKEN
$env:POWERCONTEXT_SERVER_MCP_ENABLED = "false"
$env:POWERCONTEXT_SERVER_DASHBOARD_ENABLED = "false"
$env:POWERCONTEXT_SERVER_METRICS_ENABLED = "false"
$env:POWERCONTEXT_SERVER_RUNTIME_DREAM_ENABLED = "false"
$env:PYTHONUTF8 = "1"

Write-Host "[3/3] 启动 PowerContext @ http://127.0.0.1:$Port (Ctrl+C 停止)" -ForegroundColor Green
Write-Host "      给 Dify 用: PC_BASE_URL=http://host.docker.internal:$Port  PC_TOKEN=$TOKEN" -ForegroundColor Yellow
& (Join-Path $Venv "Scripts\powercontext.exe") server run
