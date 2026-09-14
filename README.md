# push-tool

信息推送工具：几个定时 digest（行业日报 / 学术周报 / 行业周报），由 Claude 筛选成"精华"，
推送到**飞书** + 一个 **web dashboard**。

## 推送口径（2026-08 起）

情报内容**只推公司群**（`FEISHU_WEBHOOK_URL_COMPANY`）；个人群 webhook 只留给
"推送多次重试仍失败"的运维告警。

| 推送 | 时间（Asia/Shanghai） | 画像 |
| --- | --- | --- |
| 行业日报 | 工作日 07:30 | 竞对窄口径（竞对动向 + 本业，不含泛半导体上下游） |
| 学术日报 | 工作日 07:35 | 固态纳米孔（7 天滚动窗口，已发过的靠跨期去重剔除） |
| 行业周报 | 周日 20:05 | 竞对窄口径（本周日报的漏网之鱼） |
| 半月综述 ×2 | 1、15 号 09:00 | 由 `skills/` 蒸馏正文生成 |

窄口径行业日报大多数工作日筛不出东西（竞对官网更新稀疏），学术日报是工作日的兜底。
学术原为周更，改日更的另一半原因是 ingest 跟着 cron 走——周更时整周只在周日抓一次源。

改了源或画像后，用 `scripts/sync_seed_to_db.py` 同步到已有库（`seed.load` 只建不改）。
排查"为什么没推"用 `scripts/diag_pipeline.py`（只读，看条目在哪一步被砍光）和
`scripts/diag_summarize.py`（跑一遍 LLM 精筛预演，`digest_id` 传 0 = 预演下一期）。

**同一篇只推一次**：`pipeline._drop_delivered_duplicates` 拿本用户**所有**订阅的历史
投递做 pgvector 近重复比对，命中就剔除（不再有"高价值可跨订阅再发一次"的例外）。

已有部署切到这套口径：`docker compose exec -T db psql -U push -d pushtool -f - < scripts/switch_to_company_only.sql`，然后 `docker compose restart scheduler`。

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
- 飞书自定义机器人 `FEISHU_WEBHOOK_URL_COMPANY`（在群里加"自定义机器人"获取）——
  所有情报都推这个群，留空就完全不投递
- 可选 `FEISHU_WEBHOOK_URL`：只用来收"推送失败"运维告警
