-- Part B：竞品官网源加入行业日报（subscription_id=1）。与 seed/sources_industry.yaml 同步。
SET client_encoding TO 'UTF8';

INSERT INTO sources (subscription_id, kind, config_json, weight, active) VALUES
 (1,'rss','{"name":"SiMPore","url":"https://www.simpore.com/category/news/feed/"}'::jsonb,1.1,true),
 (1,'rss','{"name":"港湾半导体 (Harbor/nanofab)","url":"https://www.nanofab.com.cn/en/feed/"}'::jsonb,1.1,true),
 (1,'rss','{"name":"Quantifoil","url":"https://www.quantifoil.com/news/rss.xml"}'::jsonb,1.0,true),
 (1,'rss','{"name":"Protochips","url":"https://www.protochips.com/news/feed/"}'::jsonb,1.0,true),
 (1,'rss','{"name":"DENSsolutions","url":"https://denssolutions.com/feed/"}'::jsonb,1.0,true),
 (1,'rss','{"name":"Moxtek","url":"https://moxtek.com/feed/"}'::jsonb,0.9,true),
 (1,'html','{"name":"CleanSiN","url":"https://www.cleansin.com/applications/","keywords":["氮化硅","膜","membrane","sin","tem","nanopore","nitride","window"],"respect_robots":true,"rate_limit_s":5}'::jsonb,1.2,true),
 (1,'html','{"name":"Norcada","url":"https://www.norcada.com/news/","keywords":["membrane","sin","nitride","mems","nanopore","tem","window"],"respect_robots":true,"rate_limit_s":5}'::jsonb,1.1,true),
 (1,'html','{"name":"Ted Pella","url":"https://www.tedpella.com/Newprod.aspx","keywords":["tem","grid","membrane","nitride","support","em","microscopy"],"respect_robots":true,"rate_limit_s":5}'::jsonb,0.9,true),
 (1,'html','{"name":"Amptek","url":"https://www.amptek.com/pressreleases/press-releases","keywords":["x-ray","detector","sdd","window","silicon"],"respect_robots":true,"rate_limit_s":5}'::jsonb,0.8,true);

SELECT id, kind, active, config_json->>'name' AS name
FROM sources WHERE subscription_id = 1 ORDER BY id;
