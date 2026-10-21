# 拍卖价格采集平台

添加拍卖平台链接 → 配置（或由 AI 生成）采集规则 → 抓取 → 列表展示与价格跟踪。

架构设计见 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)。

## 当前进度（M1）

- 超级管理员登录（JWT）
- 站点管理：静态请求 / 浏览器渲染（Playwright）两种抓取方式，请求头、频率配置
- 站点登录：无需登录 / 粘贴 Cookie / 账号密码自动登录（会话复用，失效自动重新登录），凭据加密存储
- 声明式 JSON 采集规则：CSS / XPath / 正则字段、金额/时间/状态等转换、翻页；规则试跑；规则版本与回滚
- 手动触发采集，采集记录与错误信息
- 拍品列表：按站点/状态/关键词/价格筛选与排序，详情与价格变化记录

后续：AI 生成规则（M1.5）、可视化选取器与定时调度（M2）、价格曲线与导出（M3）。

## Docker 部署

```bash
cp .env.example .env   # 修改 SECRET_KEY 和 ADMIN_PASSWORD
docker compose up -d --build
```

浏览器访问 http://localhost:8080 ，使用 `.env` 中的管理员账号登录。

服务组成：`web`（nginx + 前端）、`api`（FastAPI，启动时自动执行数据库迁移）、`worker`（arq 采集任务，内置 Chromium）、`postgres`、`redis`。

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

`REDIS_URL` 为空时，采集任务直接在 API 进程内执行，无需启动 Redis 和 worker；
配置 `REDIS_URL` 后需另外运行 `uv run arq app.worker.WorkerSettings`。

前端：

```bash
cd frontend
npm install
npm run dev                            # http://localhost:5173，/api 代理到 8000 端口
```

## 采集规则示例

```json
{
  "fetch": { "wait_for": ".lot-item", "scroll": false },
  "list": {
    "item_selector": ".lot-item",
    "unique_key": "url",
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
  "pagination": { "type": "next_link", "selector": "a.next", "max_pages": 10 }
}
```

标准字段（title、url、image、current_price、start_price、deal_price、bid_count、status、start_time、end_time）存入对应列，其余字段存入扩展信息。
