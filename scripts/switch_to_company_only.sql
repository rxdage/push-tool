-- 2026-08 推送口径统一：只推公司群，个人群那套时间表迁过来，同一篇只推一次。
--
-- 与 app/seed/load.py 的 SUBSCRIPTIONS 保持同步。已有库跑这个脚本，新库直接 seed.load 即可。
-- 幂等：重复执行结果相同。
--
--   docker compose exec -T db psql -U push -d pushtool -f - < scripts/switch_to_company_only.sql
SET client_encoding TO 'UTF8';

-- 1) 行业日报：原本是个人群的宽口径（含泛半导体上下游），改用竞对窄口径画像。
--    时间表不动（工作日 07:30），条数取原公司群那份实测过的 5+12。
UPDATE subscriptions
SET interest_profile_id = (
        SELECT id FROM interest_profiles WHERE name = '超薄 SiN 膜竞对 (公司群)'
    ),
    max_deep = 5,
    max_brief = 12
WHERE name = '行业日报';

-- 2) 行业周报：同样改窄口径。时间表不动（周日 20:05）。
--    内容是"本周日报没发过的漏网之鱼"——pipeline 的跨订阅去重保证不和日报重复。
UPDATE subscriptions
SET interest_profile_id = (
        SELECT id FROM interest_profiles WHERE name = '超薄 SiN 膜竞对 (公司群)'
    )
WHERE name = '行业周报';

-- 3) 原来公司群专用的竞对日报（12:40 那档）已合并进「行业日报」，停用。
--    不删行：它的投递历史要留着，pipeline 靠它避免把公司群已经看过的内容再推一遍。
UPDATE subscriptions SET active = false WHERE name = '行业日报(公司群)';

-- 学术周报（周日 20:00 全文）不动，只是投递目标由个人群改成公司群（代码层面）。

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
