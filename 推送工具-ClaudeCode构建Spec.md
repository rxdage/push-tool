# Claude Code 构建 Spec｜信息推送工具

> 目标读者：Claude Code。按"分阶段实现清单"（第 11 节）一段段建。涉及 Anthropic API 的当前参数（模型、Batch API、SDK），建时去 https://docs.claude.com 核最新。

---

## 1. 目标与范围

个人 MVP：两个定时 digest，由 Claude 筛选成"精华"，推到**飞书** + 一个 **web dashboard**。

- **行业日报**：MEMS/半导体里聚焦"超薄 SiN 悬空膜微纳芯片平台"的情报雷达，每条带"行动/关注"标签。
- **学术周报**：固态纳米孔（solid-state nanopore），新的 + 经典的精华。

**硬约束：云中立 + 多租户就绪。** 现在只有 1 个用户，但所有数据按 `user_id` 隔离，"我的兴趣"绝不写死在代码里——它们是 `InterestProfile` 表里的行。MVP 单节点；Phase-2 再加服务号渠道、海外抓取 + 大陆服务分区。

---

## 2. 技术栈

- Python 3.11+，FastAPI + Pydantic
- Postgres 15+ + pgvector（去重 + 语义检索）
- 调度：APScheduler（进程内，cron 风格，tz-aware）；预留切换到队列（Arq/Celery/RQ）的接缝
- 筛选：Anthropic Messages API（逐条摘要/分类用 `claude-haiku-4-5`，要质量可换 `claude-sonnet-4-6`；日/周批量跑走 Message Batches API 省钱）
- 抓取：httpx + feedparser（RSS）+ trafilatura/BeautifulSoup（无 feed 的页面，少用、守 robots）
- 嵌入（去重/语义检索）：本地多语 sentence-transformers（如 `bge-m3`，中英都行，避免额外 API 依赖）
- Docker + docker-compose（app + postgres），保证 AWS / 阿里云 / 腾讯云都能落
- 前端：MVP 用 FastAPI + Jinja/HTMX 的极简 dashboard 即可

---

## 3. 仓库结构

```
push-tool/
  app/
    main.py              # FastAPI app + dashboard 路由
    config.py            # 读 env
    db.py                # async session + pgvector 初始化
    models.py            # ORM（见第 4 节）
    schemas.py           # Pydantic
    ingestion/
      base.py            # SourceAdapter ABC: fetch() -> list[RawItem]
      rss.py             # 通用 RSS
      arxiv.py           # arXiv API
      pubmed.py          # PubMed E-utilities
      semantic_scholar.py# S2（拿引用数识别经典/影响力）
      html_scrape.py     # 无 feed 的源（守 robots + 限速 + 缓存）
      registry.py        # source.kind -> adapter
    curation/
      embed.py           # 本地嵌入
      dedup.py           # url/title + 嵌入余弦阈值（pgvector）
      score.py           # 相关性打分（嵌入相似 + LLM 闸门）
      summarize.py       # Claude：摘要 + 分类（new/classic；行业:行动/关注 + 意味着什么）
      classic.py         # 经典论文：种子清单 + S2 高引用兜底
      pipeline.py        # ingest->store->dedup->score->summarize->rank->bucket
    delivery/
      base.py            # DeliveryChannel ABC: send(digest)
      feishu_bot.py      # 飞书 自定义机器人 webhook（消息卡片）
      formatter.py       # digest -> 飞书 卡片 / dashboard payload
      # wecom_bot.py / service_account.py  # Phase-2 企业微信 / 微信服务号
    scheduler.py         # 每个 subscription 一个 tz-aware cron job
    seed/
      sources_industry.yaml
      sources_academic.yaml
      classics_nanopore.yaml
      profiles.yaml       # 两个 InterestProfile 的种子
  migrations/             # alembic
  docker-compose.yml
  Dockerfile
  .env.example
  pyproject.toml
  README.md
```

---

## 4. 数据模型（多租户接缝）

全部按 `user_id` 隔离；现在 seed 一个 user。

- **User**(id, name, tz default `'Asia/Shanghai'`, created_at)
- **InterestProfile**(id, user_id, name, description, keywords[], must_have[], exclude[], notes) — 这是"精华"筛选的大脑配置
- **Subscription**(id, user_id, name, feed_type[`industry`|`academic`], interest_profile_id, schedule_cron, tz, max_deep, max_brief, max_classic, delivery_channel_ids[], active)
- **Source**(id, subscription_id, kind[`rss`|`arxiv`|`pubmed`|`s2`|`html`], config_json, weight, active)
- **Item**(id, source_id, external_id, url, title, abstract, raw_text, published_at, fetched_at, embedding `vector`, tags[], raw_json) — url/external_id 去重
- **Digest**(id, subscription_id, run_at, period_start, period_end, status)
- **DigestItem**(id, digest_id, item_id, bucket[`deep`|`brief`|`classic`], rank, summary, classification, action_label[`action`|`watch`|null], why_it_matters)
- **DeliveryLog**(id, digest_id, channel, status, response, sent_at)
- （Phase-2）**Channel**(id, user_id, kind[`feishu_bot`|`wecom_bot`|`service_account`|`email`], config_json) — MVP 阶段渠道可先走 env。

---

## 5. 两个订阅的种子配置

### 行业日报
- **schedule**：每工作日 07:30 `Asia/Shanghai`
- **max_deep 3 / max_brief 6 / max_classic 0**；目标读完 ~25 分钟（地铁）
- **InterestProfile（超薄 SiN 膜/微纳芯片平台）**
  - keywords：超薄 SiN 膜、TEM/电镜 支持膜耗材、in-situ/液体池 TEM、cryo-EM 载网、X-ray 窗、EV/外泌体/液体活检膜、固态纳米孔(watch)、微纳代工/MPW、电镜国产替代政策与采购
  - 竞品监控：CleanSin、港湾半导体、Norcada、Ted Pella、EMS、Quantifoil、SiMPore、Amptek、Moxtek、DENSsolutions、Protochips
  - must_have：与"膜/电镜耗材/国产替代/采购线索"相关
  - exclude：纯 IC 设计；与膜无关的 MEMS（麦克风/IMU/微镜）除非涉及工艺或产能
- **sources**：麦姆斯咨询、半导体行业观察、集微网、EE Times(+China)、Semiconductor Engineering、SemiWiki（RSS 优先）；政策：科技部/发改委/地方科技局/NSFC 通知（RSS 或 scrape）；采购：中国政府采购网 + 院所招标页（scrape，关键词过滤）；目标公司 IR/news
- **输出**：每条带"行动/关注"标签；"行动"项加一句 ≤25 字 "这对你意味着什么"

### 学术周报
- **schedule**：每周日 20:00 `Asia/Shanghai`
- **max_deep 5 / max_brief 10–15 / max_classic 1–2**；目标读完 ~2 小时
- **InterestProfile（固态纳米孔）**
  - keywords：solid-state nanopore、2D-material nanopore (MoS₂/graphene/hBN)、nanopore fabrication (controlled dielectric breakdown / TEM drilling / ion-beam)、DNA/protein sequencing、ionic current sensing、nanopore + machine learning
  - 优先级最高：制造工艺 / 可制造性 / 传感应用 / 小批量验证
- **sources**：arXiv API（`cond-mat.mes-hall`、`physics.bio-ph`、`physics.app-ph`）；PubMed/PMC E-utilities；bioRxiv；Semantic Scholar（引用图谱识别影响力与经典）；期刊 RSS（Nature Nanotechnology、Nature Materials、ACS Nano、Nano Letters、Small、ACS Sensors、Lab on a Chip）
- **经典**：`classics_nanopore.yaml` 人工种子清单 + S2 高引用兜底，每周轮换 1–2 篇"经典回顾"

---

## 6. 筛选 pipeline（"先读再整理"的大脑）

`fetch（所有源）→ normalize → dedup（url/title + 嵌入余弦阈值，pgvector）→ relevance score（嵌入相似度初筛 + LLM 闸门）→ 对 top-N：summarize + classify → rank → bucket（deep/brief/classic）→ format → deliver`

- summarize/classify 用 Claude Messages API，**把对应 InterestProfile 放进 system prompt** 做个性化。逐条调用可并发；日/周批量跑用 Message Batches API。
- 输出严格 JSON。**版权红线**：只存摘要片段；输出一律改写（paraphrase），单条引用不超过 15 词，绝不复制原文段落或整段摘要。

**筛选 system prompt 模板（嵌进 summarize.py）：**
```
你是为某用户做个性化情报/文献筛选的分析师。
兴趣画像：{profile.description}
关键词：{profile.keywords}
优先：{profile.must_have}
排除：{profile.exclude}

对每个候选条目：
1) 给相关性 0–1；
2) 分类 [new|classic]；行业源还要给 [action|watch]；
3) 写一条 ≤40 字的改写式摘要（绝不照抄原文，单引用 <15 词）；
4) 行业的 action 条目，再加一条 ≤25 字 why_it_matters，明确"这对超薄 SiN 膜平台意味着什么"。

只输出 JSON 数组：
[{"id":..., "relevance":0-1, "classification":"new|classic",
  "action_label":"action|watch|null", "summary":"...", "why_it_matters":"..."}]
全部改写，禁止复制来源文字。
```

---

## 7. 飞书投递（MVP）

- 飞书**自定义机器人 webhook**。在飞书群里添加"自定义机器人"，拿到 webhook URL（形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxx`），放进 `FEISHU_WEBHOOK_URL`；若开启签名校验，密钥放 `FEISHU_WEBHOOK_SECRET`。
- 发**消息卡片（interactive card）**最合适：标题 + deep 条（标题+链接+摘要+行动/关注+意味着什么）+ brief 列表；也可降级用 text/post。注意单条长度上限，超了分条发。建时去 open.feishu.cn/document 核当前卡片格式与签名算法。
- `DeliveryChannel` ABC 抽象好，Phase-2 加企业微信/服务号/邮件不动 pipeline。

---

## 8. Web dashboard（MVP，极简）

FastAPI + Jinja/HTMX。路由：`/digests`（列表）、`/digest/{id}`（阅读）、`/subscriptions` 与 `/sources`（查看/开关）、`/items`（检索，后续接 pgvector 语义检索）。全部按 `user_id` 过滤（现在单用户，auth 先打桩）。

---

## 9. 调度

APScheduler，每个 active subscription 一个 tz-aware cron job（用 `subscription.tz`）。job = 跑该订阅的筛选 pipeline → 生成 digest → 投递。要幂等、记录 run 状态。

---

## 10. env / secrets（`.env.example`）

```
ANTHROPIC_API_KEY=
FEISHU_WEBHOOK_URL=
FEISHU_WEBHOOK_SECRET=    # 若开启签名校验
DATABASE_URL=postgresql+asyncpg://...
S2_API_KEY=            # 可选
PUBMED_EMAIL=          # E-utilities 礼貌标识
TZ_DEFAULT=Asia/Shanghai
```

---

## 11. 分阶段实现清单（按序建）

1. 脚手架 + Docker + Postgres + models + alembic 迁移 + seed（user / 两个 subscription / sources / profiles）。
2. 抓取 adapters（rss、arxiv、pubmed、s2）+ registry，入库 Item。
3. 筛选 pipeline（dedup → score → summarize/classify via Claude）+ 严格 JSON。
4. 投递：feishu_bot + formatter；手动端到端跑通一个订阅。
5. 调度（tz-aware cron）两个订阅。
6. dashboard（列表/阅读/检索）+ 开关。
7. （后续）pgvector 语义去重/检索打磨；经典论文种子；Phase-2 渠道与多区域。

---

## 12. 决策与备注

- **API/RSS 优先于爬虫**；爬的源守 robots.txt + 限速 + 缓存。
- **云中立**（Docker）；部署在能够到 arXiv/PubMed 的节点（MVP 海外单节点）。Phase-2：海外抓取 + 大陆服务分区 + 服务号渠道；大陆公网 dashboard 需 ICP 备案。
- **第一天就多租户**：兴趣画像是数据（InterestProfile 行），不写死。
- **用户需自备**：`ANTHROPIC_API_KEY`；飞书自定义机器人 `FEISHU_WEBHOOK_URL`（在飞书群里加个"自定义机器人"就能拿到，得你自己点）。
