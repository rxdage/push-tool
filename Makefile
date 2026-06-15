# push-tool — 常用命令一键化。用法：make help
# 需要 Docker + docker compose。订阅 id 默认 1（行业日报），2=学术周报。
id ?= 1

.DEFAULT_GOAL := help
.PHONY: help bootstrap env up down migrate seed revision fetch run run-deliver \
        scheduler logs ps test lint psql clean

help: ## 显示所有命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
	  awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

env: ## 若无 .env 则从 .env.example 复制
	@test -f .env || (cp .env.example .env && echo "已创建 .env，请填 ANTHROPIC_API_KEY / FEISHU_WEBHOOK_URL")

bootstrap: env ## 一键初始化：起库 + 迁移 + 灌种子
	docker compose up -d db
	docker compose run --rm app alembic upgrade head
	docker compose run --rm app python -m app.seed.load
	@echo "完成。make up 起服务；make run id=2 跑学术周报。"

up: ## 起 web + scheduler（后台）
	docker compose up -d app scheduler
	@echo "dashboard: http://localhost:8000/"

down: ## 停所有容器
	docker compose down

migrate: ## 跑数据库迁移到最新
	docker compose run --rm app alembic upgrade head

seed: ## 灌种子（user / 订阅 / 源 / 画像，幂等）
	docker compose run --rm app python -m app.seed.load

revision: ## 生成迁移：make revision m="add xxx"
	docker compose run --rm app alembic revision --autogenerate -m "$(m)"

fetch: ## 抓取一个订阅的源：make fetch id=1
	docker compose run --rm app python -m app.ingestion.run $(id)

run: ## 端到端跑一个订阅（抓取→筛选→投递）：make run id=2
	docker compose run --rm app python -m app.run_subscription $(id) --fetch

run-deliver: ## 只生成 digest 不投递：make run-deliver id=2
	docker compose run --rm app python -m app.run_subscription $(id) --fetch --no-deliver

scheduler: ## 前台跑调度（看日志）
	docker compose up scheduler

logs: ## 跟随日志
	docker compose logs -f

ps: ## 容器状态
	docker compose ps

psql: ## 进 Postgres 命令行
	docker compose exec db psql -U push -d pushtool

test: ## 本地跑单测（需 pip install -e ".[dev]"）
	python -m pytest tests/ -q

lint: ## ruff 检查
	ruff check app tests

clean: ## 停容器并删数据卷（⚠ 清库）
	docker compose down -v
