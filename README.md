# 拍卖价格采集平台

添加拍卖平台链接 → 配置（或由 AI 生成）采集规则 → 定时抓取 → 列表展示与价格跟踪。

架构设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 功能

**站点与登录**
- 三种抓取方式：静态请求（httpx）、浏览器渲染（Playwright，支持 JS 页面与滚动加载）、数据接口（GET/POST JSON）
- 站点登录：无需登录 / 粘贴 Cookie / 账号密码自动登录（会话复用，失效自动重新登录），凭据加密存储
- 代理池（站点级开关，随机选择）、随机 User-Agent、请求失败自动重试

**采集规则**（四种方式产出同一种声明式 JSON 规则，均可试跑、版本化、一键回滚）
- AI 生成：只给网址，AI 分析页面生成规则并自动试跑、修正；支持 OpenAI 协议（OpenAI、DeepSeek、Gemini、通义千问等）与 Anthropic 协议（Claude 等）的任意模型
- 可视化选取：在页面快照上点击拍品、字段和"下一页"
- 接口嗅探：自动找出页面背后返回拍品列表的 JSON 接口并猜测字段
- 手写 JSON / 从规则模板导入
- 支持翻页（下一页链接 / URL 模板 / 页码参数）、详情页补充字段

**采集与监控**
- 手动采集、定时采集（cron）、盯价（只刷新进行中拍品的价格）
- 采集记录与详细日志
- 规则健康检查：连续失败判定失效，通过企业微信 / 钉钉 / 飞书 / 通用 Webhook 告警；可开启 AI 自动修复（直接启用新版本）

**数据**
- 拍品列表：按站点 / 状态 / 关键词 / 价格筛选与排序
- 拍品详情：价格走势图与变化记录
- 导出 Excel / CSV

**账号**：超级管理员（环境变量初始化，JWT 登录）。

## Docker 部署

```bash
cp .env.example .env   # 修改 SECRET_KEY 和 ADMIN_PASSWORD
docker compose up -d --build
```

浏览器访问 http://localhost:8080 ，使用 `.env` 中的管理员账号登录。

| 服务 | 说明 |
| --- | --- |
| `web` | nginx + 前端，`/api` 反向代理到 api |
| `api` | FastAPI，启动时自动执行数据库迁移 |
| `worker` | arq 采集任务执行（内置 Chromium） |
| `scheduler` | 定时任务调度 |
| `postgres` / `redis` | 数据库 / 任务队列 |

## 使用流程

1. **系统设置**（可选）：开启 AI 并添加模型（填写协议、Base URL、API Key、模型名，可"测试连接"）；配置告警 Webhook、代理池。
2. **站点管理 → AI 添加站点**：填入列表页链接即可自动生成并启用规则；
   或 **新增站点**：填写链接、登录方式，在"采集规则"中用 AI 生成 / 可视化选取 / 嗅探接口 / 手写 JSON，点"试跑"确认结果后保存。
3. 在"定时采集"中设置完整采集和盯价周期，或点击"立即采集"。
4. 在 **拍品数据** 中查看、筛选、导出；点击拍品查看价格走势。

## 本地开发

后端（需要 [uv](https://docs.astral.sh/uv/)）：

```bash
cd backend
uv sync
uv run playwright install chromium     # 浏览器模式需要
cp .env.example .env
uv run alembic upgrade head
uv run uvicorn app.main:app --reload   # http://localhost:8000/docs
uv run pytest                          # 测试；TEST_DATABASE_URL 可指定 PostgreSQL
```

`REDIS_URL` 为空时，采集任务和定时调度直接在 API 进程内运行，无需 Redis、worker 和 scheduler；
配置 `REDIS_URL` 后需另外运行 `uv run arq app.worker.WorkerSettings` 与 `uv run python -m app.scheduler`。

前端：

```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173，/api 代理到 8000 端口
```

## 采集规则参考

```jsonc
{
  "fetch": { "mode": null, "wait_for": ".lot-item", "scroll": false, "wait_ms": 0, "timeout": 30 },
  "list": {
    "item_selector": ".lot-item",          // 每个拍品的容器
    "item_selector_type": "css",           // css / xpath / jsonpath（接口模式）
    "unique_key": "url",                   // 去重字段
    "fields": [
      { "key": "title", "selector": ".title", "transform": ["trim"] },
      { "key": "url", "selector": "a", "attr": "href", "transform": ["absolute_url"] },
      { "key": "current_price", "selector": ".price", "transform": ["price"] },
      { "key": "bid_count", "selector": ".bids", "transform": ["int"] },
      { "key": "status", "selector": ".status", "transform": ["status"] },
      { "key": "end_time", "selector": ".countdown", "attr": "data-end", "transform": ["datetime"] },
      { "key": "lot_no", "type": "regex", "pattern": "lot/(\\d+)", "from": "url" }
    ]
  },
  "pagination": { "type": "next_link", "selector": "a.next", "max_pages": 10 },
  "detail": {
    "enabled": false, "url_field": "url", "max_items": 50, "only_new": true,
    "fields": [{ "key": "estimate", "selector": ".estimate", "transform": ["trim"] }]
  }
}
```

- 标准字段 title、url、image、current_price、start_price、deal_price、bid_count、status、start_time、end_time 存入对应列，其余字段存入扩展信息（导出时展开为列）。
- transform：`trim`、`price`（解析金额与币种，支持"万/亿"）、`number`、`int`、`datetime`、`absolute_url`、`status`、`lower`、`{"name": "regex_replace", "pattern": "...", "repl": "..."}`。
- 接口模式：`fetch` 设 `"mode": "api", "url": "...", "method": "GET|POST", "body": {...}`，`item_selector` 如 `$.data.list[*]`，字段 `type: "jsonpath"`，翻页用 `url_template` 或 `{"type": "page_param", "param": "pageNo"}`。
