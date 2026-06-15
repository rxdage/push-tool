# push-tool

个人信息推送工具：两个定时 digest（行业日报 / 学术周报），由 Claude 筛选成"精华"，
推送到**飞书** + 一个 **web dashboard**。

> 设计与范围见 [`推送工具-ClaudeCode构建Spec.md`](推送工具-ClaudeCode构建Spec.md)。
> 硬约束：**云中立 + 多租户就绪**——兴趣画像是 `InterestProfile` 表里的数据行，不写死在代码里。

## 构建进度（Spec 第 11 节）

- [x] **Phase 1** 脚手架 + Docker + Postgres + models + alembic 迁移 + seed
- [x] **Phase 2** 抓取 adapters（rss / arxiv / pubmed / s2 / html）+ registry，入库 Item
- [x] **Phase 3** 筛选 pipeline（dedup → score → summarize/classify via Claude）+ 严格 JSON
- [x] **Phase 4** 投递：feishu_bot + formatter；端到端跑通一个订阅
- [x] **Phase 5** 调度（tz-aware cron）两个订阅
- [x] **Phase 6** dashboard（列表 / 阅读 / 检索）+ 开关
- [x] **Phase 7** pgvector 语义去重/检索打磨；经典论文种子入库
      —— ✅ 经典论文 Item 化入库 + 轮换 + S2 高引用兜底（[`classic.py`](app/curation/classic.py)）；
      ✅ 跨期语义去重（[`pipeline._drop_delivered_duplicates`](app/curation/pipeline.py)）；
      ✅ dashboard `/items?mode=semantic` pgvector 语义检索（[`dashboard.py`](app/dashboard.py)）。
- [ ] Phase-2（更后续）企业微信/微信服务号/邮件渠道；海外抓取 + 大陆服务分区。

## 一键启动

```powershell
# Windows / PowerShell
./scripts/bootstrap.ps1                 # 起库 + 迁移 + 灌种子
./scripts/bootstrap.ps1 -Run 2 -Up      # 再端到端跑学术周报 + 起 web/scheduler
```

```bash
# Linux / macOS（部署节点）
bash scripts/bootstrap.sh               # 起库 + 迁移 + 灌种子
bash scripts/bootstrap.sh 2 --up        # 再跑学术周报 + 起 web/scheduler
# 或用 Make：
make bootstrap && make up               # make help 看全部命令
```

> 首次会拉 `bge-m3` 嵌入模型（约 2GB，缓存在 `model_cache` 卷）。
> `.env` 不存在时脚本会从 `.env.example` 生成并提示你填 key 后重跑。

## 手动步骤（等价于上面）

```bash
cp .env.example .env        # 填 ANTHROPIC_API_KEY / FEISHU_WEBHOOK_URL
docker compose up -d --wait db                       # 起 Postgres(pgvector)
docker compose run --rm app alembic upgrade head     # 建表
docker compose run --rm app python -m app.seed.load  # 灌种子（user/订阅/源/画像）
docker compose up -d app    # 起 web，访问 http://localhost:8000/
```

Dashboard：http://localhost:8000/ （→ `/digests`）。健康检查：`GET /health`。

## 手动 / 调度运行

```bash
# 抓取一个订阅的源 → 入库 Item
docker compose run --rm app python -m app.ingestion.run 1

# 端到端跑一个订阅：抓取 → 筛选(Claude) → 投递飞书
docker compose run --rm app python -m app.run_subscription 1 --fetch
docker compose run --rm app python -m app.run_subscription 1 --no-deliver   # 只生成不投递
docker compose run --rm app python -m app.run_subscription 1 --batch        # summarize 走 Batch API 省钱

# 调度（已在 docker-compose 的 scheduler 服务里常驻）：每订阅一个 tz-aware cron job
docker compose up scheduler
```

## 本地开发（不用 Docker）

```bash
python -m venv .venv && . .venv/Scripts/activate   # Windows PowerShell: .venv\Scripts\Activate.ps1
pip install -e ".[dev]"
# 需要一个本地 Postgres(带 pgvector)，把 DATABASE_URL 指过去
alembic upgrade head
python -m app.seed.load
uvicorn app.main:app --reload
```

## 数据模型

见 [`app/models.py`](app/models.py)（Spec 第 4 节）。全部按 `user_id` 隔离。

## 你需要自备

- `ANTHROPIC_API_KEY`
- 飞书自定义机器人 `FEISHU_WEBHOOK_URL`（在飞书群里加"自定义机器人"获取）
