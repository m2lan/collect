# 拍卖价格采集平台

添加拍卖平台链接 → 配置（或由 AI 生成）采集规则 → 定时抓取 → 列表展示与价格跟踪。

架构设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 功能

**站点与登录**
- 三种抓取方式：静态请求（httpx）、浏览器渲染（Playwright，支持 JS 页面与滚动加载）、数据接口（GET/POST JSON）；浏览器模式还支持**接口捕获**（打开页面并截取页面自己发出的 JSON 请求，适合签名接口的站点，如淘宝）
- 站点登录：无需登录 / 粘贴 Cookie / 账号密码自动登录 / **远程浏览器人工登录**（在网页里操作服务器上的真实浏览器，完成扫码、验证码、短信或人机验证后保存会话）；凭据加密存储
- **登录过期提醒**：检测到会话失效时告警一次，重新登录后发送恢复通知
- 反爬加固：浏览器指纹伪装、同域名限速（多 worker 共享）、浏览器复用池、代理池、随机 UA（仅 Chromium 系，避免指纹矛盾）、自动重试
- **内置平台模板**：阿里资产 / 淘宝拍卖、AllSurplus，选择即用

**采集规则**（多种方式产出同一种声明式 JSON 规则，均可试跑、版本化、一键回滚）
- AI 生成：只给网址，AI 分析页面生成规则并自动试跑、修正；支持 OpenAI 协议（OpenAI、DeepSeek、Gemini、通义千问等）与 Anthropic 协议（Claude 等）的任意模型
- 可视化选取：在页面快照上点击拍品、字段和"下一页"
- 接口嗅探：自动找出页面背后返回拍品列表的 JSON 接口并猜测字段（必要时自动切换为接口捕获）
- 手写 JSON / 从规则模板导入 / **Chrome 浏览器插件**一键把当前页面和登录 Cookie 添加为站点
- 支持翻页（下一页链接 / 点击 JS 翻页按钮 / URL 模板 / 页码参数 / 滚动加载）、详情页补充字段（保证金、评估价等）
- 支持"拍卖会 / 专场列表 → 专场内拍品"两级结构：给首页或拍卖会列表页即可，AI 会识别并自动逐个进入专场

**采集与监控**
- 手动采集、定时采集（cron）、盯价（只刷新进行中拍品的价格）、**临近结束加密盯价**
- **采集看板**：成功率、耗时、错误类型分布、站点健康度
- 规则健康检查：连续失败判定失效，通过企业微信 / 钉钉 / 飞书 / 通用 Webhook 告警；可开启 AI 自动修复
- **订阅提醒**：按关键词 / 排除词 / AI 标签 / 站点 / 价格区间订阅，新上架、价格变化、达到目标价、即将结束时推送

**数据与分析**
- 拍品列表：**全文搜索**（多关键词、同义词、标签；PostgreSQL 下使用 pg_trgm 索引）、按站点 / 状态 / 品类 / 年代 / 价格筛选与排序，导出 Excel / CSV
- 拍品详情：价格走势图、变化记录、**跨平台同款比价**
- **AI 结构化标注**：自动提取品类、年代、材质、品牌等标签
- **行情分析**：成交价走势、品类分布、站点对比，生成周报 / 月报
- **自然语言查数据**：如"最近 30 天翡翠手镯成交均价"，AI 转成安全的查询计划执行（不直接执行 SQL）

**系统**
- 多用户与角色：管理员 / 操作员 / 只读，**操作日志**审计
- **定时备份**：每天 pg_dump，按天数保留，可在页面手动触发与下载

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
| `scheduler` | 定时任务调度（定时采集、临近结束盯价、即将结束提醒） |
| `backup` | 每天定时 pg_dump 到 `./backups`（`BACKUP_TIME`、`BACKUP_KEEP_DAYS`），支持页面手动触发 |
| `postgres` / `redis` | 数据库 / 任务队列 |

## 使用流程

1. **系统设置**（可选）：开启 AI 并添加模型（填写协议、Base URL、API Key、模型名，可"测试连接"）；配置告警 Webhook、代理池。
2. **站点管理 → AI 添加站点**：填入列表页链接即可自动生成并启用规则；
   或 **新增站点**：填写链接、登录方式，在"采集规则"中用 AI 生成 / 可视化选取 / 嗅探接口 / 手写 JSON，点"试跑"确认结果后保存。
3. 在"定时采集"中设置完整采集和盯价周期，或点击"立即采集"。
4. 在 **拍品数据** 中查看、筛选、导出；点击拍品查看价格走势与同款比价。
5. 在 **订阅提醒** 中订阅关注的拍品；在 **行情分析** 中查看走势、生成报告或用自然语言提问。

需要扫码 / 验证码登录的平台：站点登录方式选"会话（远程登录）"，点"远程登录"在弹出的浏览器中完成登录并保存。
也可以安装 `extension/` 下的 Chrome 插件（见 [extension/README.md](extension/README.md)），在已登录的页面上一键添加站点。

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
  "pagination": { "type": "next_link", "selector": "a.next", "max_pages": 10 },  // JS 按钮翻页用 "click"
  "detail": {
    "enabled": false, "url_field": "url", "max_items": 50, "only_new": true,
    "fields": [{ "key": "estimate", "selector": ".estimate", "transform": ["trim"] }]
  }
}
```

- 标准字段 title、url、image、current_price、start_price、deal_price、bid_count、status、start_time、end_time 存入对应列，其余字段存入扩展信息（导出时展开为列）。
- transform：`trim`、`price`（解析金额与币种，支持"万/亿"）、`number`、`int`、`datetime`、`absolute_url`、`status`、`lower`、`{"name": "regex_replace", "pattern": "...", "repl": "..."}`。
- 翻页：`next_link`（带 href 的下一页链接）、`click`（点击"下一页"按钮，浏览器模式）、`url_template`（`?page={page}`）、`page_param`（接口页码参数）；无限滚动用 `fetch.scroll: true`。翻页后没有新拍品会自动停止。
- 详情页字段：`detail.fields` 的选择器相对整个详情页；"保证金：¥5,000"这类信息推荐用 `{"type": "regex", "pattern": "保证金[：:\\s]*([¥￥]?[\\d,.]+)", "transform": ["price"]}` 从整页文本匹配。
- 接口模式：`fetch` 设 `"mode": "api", "url": "...", "method": "GET|POST", "body": {...}`，`item_selector` 如 `$.data.list[*]`，字段 `type: "jsonpath"`，翻页用 `url_template` 或 `{"type": "page_param", "param": "pageNo"}`。
- 入口页：站点链接是拍卖会 / 专场列表（每个专场点进去才是拍品）时，设 `"entry": {"link_selector": ".event a.view", "max_links": 10}`，采集时先从入口页找出各专场链接，再逐个按 `list` 与 `pagination` 抓取；AI 生成规则时会自动识别这类页面。
- 接口捕获：浏览器模式设 `"fetch": {"capture": "接口 URL 正则", "capture_body": "可选，响应需包含的文本"}`，`item_selector` 用 JSONPath；字段可用 `{"type": "template", "template": "https:{auctionLink}"}` 拼接，`map` 转换把原始值映射为标准状态。
