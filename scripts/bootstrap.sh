#!/usr/bin/env bash
# push-tool 一键初始化（Linux / macOS 部署节点）。
# 起 Postgres → 迁移 → 灌种子；可选参数：订阅 id 则端到端跑该订阅。
#   ./scripts/bootstrap.sh          # 仅初始化
#   ./scripts/bootstrap.sh 2        # 初始化后跑学术周报（抓取→筛选→投递）
#   ./scripts/bootstrap.sh 2 --up   # 再起 web + scheduler
set -euo pipefail
cd "$(dirname "$0")/.."

RUN_ID="${1:-0}"
UP="${2:-}"

echo "==> 检查 Docker ..."
docker compose version >/dev/null

if [ ! -f .env ]; then
  cp .env.example .env
  echo "已从 .env.example 创建 .env —— 请填 ANTHROPIC_API_KEY / FEISHU_WEBHOOK_URL 后重跑。"
  exit 1
fi

echo "==> 起 Postgres 并等待健康 ..."
docker compose up -d --wait db

echo "==> 跑数据库迁移 ..."
docker compose run --rm app alembic upgrade head

echo "==> 灌种子（user / 订阅 / 源 / 画像）..."
docker compose run --rm app python -m app.seed.load

if [ "$RUN_ID" -gt 0 ] 2>/dev/null; then
  echo "==> 端到端跑订阅 #$RUN_ID（抓取→筛选→投递）..."
  docker compose run --rm app python -m app.run_subscription "$RUN_ID" --fetch
fi

if [ "$UP" = "--up" ]; then
  echo "==> 起 web + scheduler ..."
  docker compose up -d app scheduler
  echo "dashboard: http://localhost:8000/"
fi

echo ""
echo "完成 ✅"
echo "  dashboard:  docker compose up -d app  ->  http://localhost:8000/"
echo "  跑一个订阅:  docker compose run --rm app python -m app.run_subscription 2 --fetch"
echo "  常驻调度:    docker compose up -d scheduler"
