-- 2026-08-10：学术订阅由周更改成工作日日更，给公司群工作日打底。
--
-- 背景：窄口径行业日报最近 11 期有 7 期 0 条（竞对官网更新太稀），而公司群工作日
-- 原本的内容来源「每日学术精选」已随口径统一取消、且库存清零（129 条全推过）。
-- 与 app/seed/load.py 的 SUBSCRIPTIONS 保持同步。幂等。
--
--   docker compose exec -T db psql -U push -d pushtool -f - < scripts/academic_to_daily.sql
SET client_encoding TO 'UTF8';

-- 周日 20:00 全文 → 每工作日 07:35（错开行业日报 07:30，避免飞书同刻限流）。
-- 条数从 5+15+2 摊到 2+3+1，一周总量与原来相当。
-- 回看窗口仍是 7 天（app/curation/pipeline.py 的 LOOKBACK_DAYS），已发过的靠跨期去重剔除。
UPDATE subscriptions
SET name = '学术日报',
    schedule_cron = '35 7 * * 1-5',
    max_deep = 2,
    max_brief = 3,
    max_classic = 1
WHERE name IN ('学术周报', '学术日报')
  AND feed_type = 'academic';

SELECT s.id,
       s.name,
       s.active,
       s.schedule_cron,
       p.name AS profile,
       s.max_deep,
       s.max_brief,
       s.max_classic
FROM subscriptions s
JOIN interest_profiles p ON p.id = s.interest_profile_id
ORDER BY s.id;
