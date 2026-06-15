-- Part A：行业日报源实测修正（与 seed/sources_industry.yaml 同步）
SET client_encoding TO 'UTF8';

-- 麦姆斯咨询：无可用 RSS，改 html 抓取首页 + 关键词过滤
UPDATE sources
SET kind = 'html',
    config_json = '{"name":"麦姆斯咨询","url":"https://www.memsconsulting.com/","keywords":["膜","氮化硅","电镜","透射电镜","纳米孔","微纳","晶圆","刻蚀","光刻","MEMS","SiN","TEM"],"respect_robots":true,"rate_limit_s":5}'::jsonb
WHERE config_json->>'name' = '麦姆斯咨询';

-- 集微网：无可用 RSS，改 html 抓取首页 + 关键词过滤
UPDATE sources
SET kind = 'html',
    config_json = '{"name":"集微网 (JW Insights)","url":"https://www.laoyaoba.com/","keywords":["膜","氮化硅","电镜","纳米孔","微纳","晶圆","刻蚀","MEMS","SiN","TEM"],"respect_robots":true,"rate_limit_s":5}'::jsonb
WHERE config_json->>'name' = '集微网 (JW Insights)';

-- overseas 节点不可达（geo/证书），停用待 Phase-2 大陆分区
UPDATE sources SET active = false
WHERE config_json->>'name' IN ('半导体行业观察', 'EE Times China', 'NSFC 通知');

SELECT id, kind, active, config_json->>'name' AS name
FROM sources WHERE subscription_id = 1 ORDER BY id;
