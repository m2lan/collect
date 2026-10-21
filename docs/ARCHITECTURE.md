# 拍卖价格采集平台 — 架构方案（v0.1 草案）

## 1. 目标与范围

| 能力 | 说明 |
| --- | --- |
| 站点管理 | 在平台中添加拍卖平台/拍品列表页链接，配置请求方式（静态/浏览器渲染/接口）、Cookie、频率 |
| 采集规则 | 用户指定"要抓什么"：列表项、字段（标题、当前价、起拍价、成交价、出价次数、结束时间、图片、链接…）、翻页、详情页 |
| 采集执行 | 手动触发 + 定时调度；失败重试、限速、日志 |
| 数据展示 | 拍品列表（筛选/排序/搜索/导出），价格历史曲线，采集记录 |

非目标（首期不做）：登录验证码破解、分布式大规模爬取、多租户计费。

## 2. 总体架构

```
┌──────────────────────────── Web 前端 (Vue3 + Element Plus) ────────────────────────────┐
│  站点管理   规则配置(可视化选取器)   拍品列表/价格曲线   任务与日志                      │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                                        │ REST / JSON
┌───────────────────────────────────────▼────────────────────────────────────────────────┐
│                         API 服务 (Python · FastAPI)                                     │
│  Source API │ Rule API │ Item API │ Task API │ Preview API(规则试跑/页面快照)           │
└──────┬─────────────────────────────┬──────────────────────────────┬────────────────────┘
       │ SQLAlchemy                  │ 入队 (arq / Redis)            │
┌──────▼──────┐               ┌──────▼──────┐                ┌──────▼───────────────────┐
│ PostgreSQL  │◄──── 写入 ────│  Redis      │──── 消费 ─────►│ 采集 Worker              │
│ 配置/数据   │               │ 队列/锁/限速 │                │  ├ Fetcher(httpx/Playwright)│
└─────────────┘               └─────────────┘                │  ├ Extractor(CSS/XPath/JSON)│
       ▲                             ▲                       │  ├ Normalizer(价格/时间)    │
       │                             │                       │  └ Pipeline(去重/入库/历史) │
       │                      ┌──────┴──────┐                └──────────────────────────┘
       └──────────────────────│ Scheduler   │ (APScheduler，按规则 cron 投递任务)
                              └─────────────┘
```

选型理由：
- **Python**：爬虫生态最成熟（httpx、Playwright、parsel/lxml、dateparser）。
- **FastAPI**：轻量、自带 OpenAPI 文档，前后端分离方便。
- **Playwright**：拍卖站大多是 JS 渲染 + 倒计时动态刷新，必须支持浏览器模式。
- **arq + Redis**：异步任务队列，比 Celery 轻，契合 asyncio 的 httpx/Playwright。
- **PostgreSQL**：JSONB 存放规则与原始字段，便于扩展。开发期可用 SQLite。
- **部署**：docker-compose（web / api / worker / scheduler / postgres / redis）。

## 3. 核心设计："指定要抓取的内容"

### 3.1 规则模型（声明式 JSON，平台无关）

```jsonc
{
  "fetch": { "mode": "browser", "wait_for": ".lot-item", "scroll": true, "timeout": 30 },
  "list": {
    "item_selector": ".lot-item",           // 每个拍品的容器
    "fields": [
      { "key": "title",    "selector": ".title",     "type": "css",   "attr": "text" },
      { "key": "price",    "selector": ".cur-price", "type": "css",   "attr": "text", "transform": ["price"] },
      { "key": "end_time", "selector": ".countdown", "type": "css",   "attr": "data-end", "transform": ["datetime"] },
      { "key": "url",      "selector": "a",          "type": "css",   "attr": "href",  "transform": ["absolute_url"] },
      { "key": "lot_no",   "selector": "",           "type": "regex", "pattern": "lot/(\\d+)", "from": "url" }
    ],
    "unique_key": "url"                     // 用于去重/价格历史关联
  },
  "pagination": { "type": "next_link", "selector": "a.next", "max_pages": 10 },
  // 也可 "type": "url_template", "template": "...?page={page}"
  "detail": { "enabled": false, "url_field": "url", "fields": [] }
}
```

- 选择器类型：`css` / `xpath` / `jsonpath`（接口模式）/ `regex`。
- transform 管道：`trim`、`price`（"¥1,234.00"→1234.00 + 币种）、`datetime`、`absolute_url`、`number`、自定义正则替换。
- 标准字段（title / current_price / start_price / deal_price / bid_count / status / start_time / end_time / image / url）映射到结构化列，其余字段进 `extra` JSONB。

### 3.2 可视化选取器（降低配置门槛）

1. 用户输入 URL → Preview API 用 Playwright 打开页面，返回清洗后的 HTML 快照（去 script，资源改为绝对地址）。
2. 前端在沙箱 iframe 中渲染快照，注入选取脚本：鼠标悬停高亮、点击元素。
3. 点击一个拍品的"价格"→ 自动生成候选 CSS 选择器；再点第二个同类拍品 → 推导公共的 `item_selector` 与相对字段选择器。
4. "试跑"按钮：后端按当前规则抽取首页，前端表格即时展示结果，所见即所得后保存。

高级用户可直接编辑 JSON 规则；常见平台可沉淀为**规则模板**一键套用。

### 3.3 抓取模式

| 模式 | 适用 | 实现 |
| --- | --- | --- |
| `static` | 服务端渲染页面 | httpx + parsel |
| `browser` | SPA/JS 渲染、需滚动加载 | Playwright（浏览器上下文池复用） |
| `api` | 页面背后有 JSON 接口 | httpx + JSONPath（可在浏览器模式下自动嗅探 XHR 推荐使用） |

## 4. 数据模型

```
source            站点/采集源
  id, name, base_url, start_url, fetch_mode, headers(JSONB), cookies(加密),
  rate_limit(每秒请求数), enabled, created_at

rule              采集规则（版本化）
  id, source_id, version, config(JSONB, 见 3.1), is_active, created_at

schedule          调度
  id, source_id, cron, enabled, next_run_at

crawl_run         一次采集执行
  id, source_id, rule_version, trigger(manual/cron), status(pending/running/success/failed),
  pages, items_found, items_new, items_updated, error, started_at, finished_at

lot               拍品（按 source_id + unique_key 唯一）
  id, source_id, unique_key, title, url, image,
  current_price, start_price, deal_price, currency, bid_count,
  status(upcoming/live/ended/unsold), start_time, end_time,
  extra(JSONB), first_seen_at, last_seen_at

price_snapshot    价格历史（仅价格/出价数/状态变化时写入）
  id, lot_id, crawl_run_id, price, bid_count, status, captured_at
```

## 5. 采集流程

```
Scheduler/手动 ──► 投递 crawl_job(source_id) ──► Worker
  1. 加分布式锁（同一 source 同时只跑一个）
  2. 读取 active rule → Fetcher 拉取页面（限速 + 重试 3 次，指数退避）
  3. Extractor 抽列表项 → Normalizer 清洗
  4. 翻页循环，直到无下一页 / max_pages
  5. (可选) 详情页任务扇出
  6. Pipeline：按 unique_key upsert lot；价格变化写 price_snapshot
  7. 更新 crawl_run 统计；失败时保存原始 HTML 片段便于排查
```

对正在拍卖的拍品，可配置更高频的"盯价"任务，只刷新 `status=live` 的拍品。

## 6. 前端页面

1. **站点管理**：列表、新增/编辑、启停、立即采集。
2. **规则配置**：可视化选取器 + JSON 编辑器 + 试跑结果表格。
3. **拍品列表**：按站点/状态/价格区间/结束时间筛选，关键词搜索，排序，CSV/Excel 导出。
4. **拍品详情**：基本信息 + 价格历史折线图（ECharts）。
5. **采集记录**：每次运行的状态、数量、耗时、错误日志。

## 7. 稳定性与合规

- 限速：按 source 令牌桶（Redis），默认 1 req/s；随机 UA；可选代理池。
- 登录态：支持粘贴 Cookie（加密存储），或 Playwright 保存的 storage_state。
- 规则失效检测：连续 N 次抽取 0 条 / 关键字段为空率过高 → 标记告警。
- 合规：尊重目标站 robots 与服务条款，仅采集公开信息，控制频率。

## 8. 目录结构（规划）

```
collect/
├── backend/
│   ├── app/
│   │   ├── api/            # FastAPI 路由
│   │   ├── models/         # SQLAlchemy 模型
│   │   ├── schemas/        # Pydantic 模型（含规则 schema 校验）
│   │   ├── crawler/
│   │   │   ├── fetchers/   # static / browser / api
│   │   │   ├── extractor.py
│   │   │   ├── transforms.py
│   │   │   └── pipeline.py
│   │   ├── worker.py       # arq worker
│   │   └── scheduler.py
│   ├── alembic/            # 数据库迁移
│   └── tests/
├── frontend/               # Vue3 + Vite + Element Plus + ECharts
├── docker-compose.yml
└── docs/
```

## 9. 迭代计划

| 阶段 | 内容 | 产出 |
| --- | --- | --- |
| M1 MVP | 站点 CRUD、JSON 规则、static+browser 抓取、手动触发、拍品列表 | 能添加链接→配置规则→抓取→列表展示 |
| M2 | 可视化选取器、规则试跑、翻页、定时调度、采集日志 | 非技术用户可配置 |
| M3 | 价格历史与曲线、盯价任务、导出、规则失效告警 | 价格跟踪 |
| M4 | 详情页、API 模式与 XHR 嗅探、代理池、规则模板库 | 覆盖更多平台 |

## 10. 待确认问题

1. 目标拍卖平台有哪些（如阿里拍卖、京东拍卖、雅昌、海外 eBay/Yahoo 等）？是否需要登录？
2. 部署方式：单机 docker-compose 是否满足？预计站点数量与采集频率？
3. 技术栈偏好：后端 Python 是否可以？前端 Vue 还是 React？
4. 是否需要多用户/权限管理？
