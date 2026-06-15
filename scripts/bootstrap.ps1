<#
.SYNOPSIS
  push-tool 一键初始化（Windows / PowerShell）。
.DESCRIPTION
  起 Postgres → 迁移 → 灌种子；可选 -Run <id> 端到端跑一个订阅。
.EXAMPLE
  ./scripts/bootstrap.ps1
  ./scripts/bootstrap.ps1 -Run 2     # 初始化后直接跑学术周报
#>
param(
    [int]$Run = 0,          # 传订阅 id 则初始化后端到端跑该订阅（含投递）
    [switch]$Up             # 初始化后起 web + scheduler
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

Write-Host "==> 检查 Docker ..." -ForegroundColor Cyan
docker compose version | Out-Null
if ($LASTEXITCODE -ne 0) { throw "未找到 docker compose，请先装 Docker Desktop。" }

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "已从 .env.example 创建 .env —— 请填 ANTHROPIC_API_KEY / FEISHU_WEBHOOK_URL 后重跑。" -ForegroundColor Yellow
    exit 1
}

Write-Host "==> 起 Postgres 并等待健康 ..." -ForegroundColor Cyan
docker compose up -d --wait db

Write-Host "==> 跑数据库迁移 ..." -ForegroundColor Cyan
docker compose run --rm app alembic upgrade head

Write-Host "==> 灌种子（user / 订阅 / 源 / 画像）..." -ForegroundColor Cyan
docker compose run --rm app python -m app.seed.load

if ($Run -gt 0) {
    Write-Host "==> 端到端跑订阅 #$Run（抓取→筛选→投递）..." -ForegroundColor Cyan
    docker compose run --rm app python -m app.run_subscription $Run --fetch
}

if ($Up) {
    Write-Host "==> 起 web + scheduler ..." -ForegroundColor Cyan
    docker compose up -d app scheduler
    Write-Host "dashboard: http://localhost:8000/" -ForegroundColor Green
}

Write-Host "`n完成 ✅" -ForegroundColor Green
Write-Host "  dashboard:    docker compose up -d app  →  http://localhost:8000/"
Write-Host "  跑一个订阅:    docker compose run --rm app python -m app.run_subscription 2 --fetch"
Write-Host "  常驻调度:      docker compose up -d scheduler"
