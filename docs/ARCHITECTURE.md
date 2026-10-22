# 拍卖价格采集平台 — 架构方案（v1.0，已全部实现）

## 1. 目标与范围

| 能力 | 说明 |
| --- | --- |
| 站点管理 | 在平台中添加拍卖平台/拍品列表页链接，配置请求方式（静态/浏览器渲染/接口）、Cookie、频率 |
| 采集规则 | 用户指定"要抓什么"：列表项、字段（标题、当前价、起拍价、成交价、出价次数、结束时间、图片、链接…）、翻页、详情页 |
| AI 生成规则 | 只提供网址，AI 解析页面自动生成 JSON 规则；功能可开关；支持任意 OpenAI / Anthropic 协议兼容模型 |
| 采集执行 | 手动触发 + 定时调度；失败重试、限速、日志 |
| 数据展示 | 拍品列表（筛选/排序/搜索/导出），价格历史曲线，采集记录 |

非目标（首期不做）：自动破解验证码、分布式大规模爬取、多用户/多角色权限（首期仅超级管理员）、本地模型接入。

## 2. 总体架构

```
┌──────────────────────────── Web 前端 (Vue3 + Element Plus) ────────────────────────────┐
│  站点管理  规则配置(可视化选取器/AI生成)  拍品列表/价格曲线  任务与日志  系统设置(AI模型)  │
└───────────────────────────────────────┬────────────────────────────────────────────────┘
                                        │ REST / JSON
┌───────────────────────────────────────▼────────────────────────────────────────────────┐
│                         API 服务 (Python · FastAPI)                                     │
│  Source API │ Rule API │ Item API │ Task API │ Preview API(试跑/快照) │ AI API(生成/模型)  │
└──────┬─────────────────────────────┬──────────────────────────────┬────────────────────┘
       │ SQLAlchemy                  │ 入队 (arq / Redis)            │
┌──────▼──────┐               ┌──────▼──────┐                ┌──────▼───────────────────┐
│ PostgreSQL  │◄──── 写入 ────│  Redis      │──── 消费 ─────►│ 采集 Worker              │
│ 配置/数据   │               │ 队列/锁/限速 │                │  ├ Fetcher(httpx/Playwright)│
│             │               │             │                │  ├ AI RuleGenerator ──► LLM │
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
- **openai / anthropic 官方 SDK**：分别对接两种协议，均支持自定义 `base_url`。
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

### 3.4 AI 自动生成规则（可开关）

用户只给一个网址，AI 分析页面后产出 3.1 中的 JSON 规则，人工只需确认/微调。

**设计原则：AI 只负责"生成规则"，不参与每次采集。** 规则生成后，日常采集仍由确定性的 Extractor 执行——速度快、零 token 成本、结果可复现。AI 仅在新建站点、规则失效修复时调用。

#### 生成流程

```
用户输入 URL ──► [AI 开关检查] ──► Playwright 打开页面
  1. 页面采集：渲染后 HTML + 截图(可选，多模态模型) + 嗅探到的 XHR JSON 响应
  2. 预处理压缩（控制 token）：
     - 删除 script/style/svg/注释/隐藏元素，只保留 class/id/href/src/data-* 属性
     - 识别重复兄弟结构（候选拍品列表），只保留前 3 个样本，其余折叠为 "<!-- ×N similar -->"
     - 超长文本截断，整体压到 ~30k token 以内
  3. 调用 LLM：系统提示 + 规则 JSON Schema + 压缩后 HTML/接口样本
     → 通过 tool calling / JSON Schema 结构化输出返回规则
  4. 校验：Pydantic 校验规则结构；选择器语法检查
  5. 试跑：用生成的规则在当前页面上抽取
  6. 自我修正（最多 3 轮）：抽取 0 条、关键字段（标题/价格）空值率高、价格无法解析、
     实际翻到第 2 页失败时，把错误信息 + 实际抽取样本回传给 LLM 修正；翻页始终失败则退回只抓第一页
  6.5 详情页阶段：管理员要求的字段在列表页不存在（AI 通过 missing_fields 报告）或勾选"同时分析详情页"时，
     打开前 2 个拍品的详情页，由 LLM 生成详情字段规则并在两页上试跑修正
  7. 返回前端：规则 + 试跑结果表格 + AI 说明（每个字段为什么这样选）
  8. 试跑通过即保存为新规则版本并启用（origin=ai）；管理员可继续微调或回滚
```

#### 触发场景

| 场景 | 说明 |
| --- | --- |
| 新建站点 | 添加 URL 时勾选"AI 生成规则"，替代可视化选取器的手工操作 |
| 自然语言补充 | 用户可附加说明，如"还要抓保证金和围观人数"、"只要已成交的" |
| 规则失效修复 | 失效检测触发后，可配置自动调用 AI 重新生成；试跑通过即直接启用为新版本（无审核环节，可一键回滚到旧版本） |
| 优先选接口 | 若嗅探到返回拍品数据的 JSON 接口，AI 优先生成 `api` 模式规则（更稳定） |

#### 开关粒度

- **全局开关**：系统设置中开启/关闭 AI 功能；关闭后前端隐藏 AI 入口，后端接口返回 403。
- **站点级开关**：某个站点可单独禁止 AI 自动修复（例如敏感站点）。
- 未配置任何可用模型时，AI 功能自动不可用。

#### 多模型 / 多协议支持

抽象一个 `LLMProvider` 接口，按**协议**而不是按厂商实现，因此任何兼容这两种协议的模型都能接入：

```python
class LLMProvider(Protocol):
    async def generate_structured(
        self, system: str, messages: list[Message], schema: dict,
        images: list[bytes] | None = None,
    ) -> LLMResult: ...   # LLMResult: data(dict) / raw_text / usage(tokens)
```

| 协议 | 实现 | 可接入的模型示例 |
| --- | --- | --- |
| `openai` | 官方 `openai` SDK，`/v1/chat/completions`，可配 `base_url` | OpenAI、DeepSeek、通义千问、Kimi、智谱、豆包、Gemini（OpenAI 兼容端点）、各类中转 |
| `anthropic` | 官方 `anthropic` SDK，`/v1/messages`，可配 `base_url` | Claude 系列、兼容 Anthropic 协议的服务 |

结构化输出策略（按能力降级）：
1. 优先 **tool calling**：把规则 Schema 作为工具参数定义（OpenAI `tools` / Anthropic `tools.input_schema`），强制调用该工具；
2. 次选 **JSON 模式**：OpenAI `response_format=json_schema/json_object`；
3. 兜底 **文本解析**：要求输出 ```json 代码块，后端提取并校验。

每个模型配置可声明能力位（是否支持 tools、是否支持图片），以适配能力较弱的兼容模型。

模型配置项（系统设置页面管理，可配多个，指定一个默认）：

```jsonc
{
  "name": "DeepSeek-V3",
  "protocol": "openai",                 // openai | anthropic
  "base_url": "https://api.deepseek.com/v1",
  "api_key": "sk-***",                  // 加密存储，接口不回显明文
  "model": "deepseek-chat",
  "max_tokens": 8192,
  "temperature": 0,
  "supports_tools": true,
  "supports_vision": false,
  "timeout": 120,
  "is_default": true,
  "enabled": true
}
```

配置页提供"测试连接"按钮（发送一个最小请求验证 key/模型/结构化输出可用）。

#### 安全与成本

- API Key 使用应用主密钥（环境变量）对称加密入库，日志脱敏。
- 页面内容可能含提示注入：AI 输出只是**声明式规则**（选择器 + 白名单 transform），不包含可执行代码，并经 Schema 严格校验，风险可控。
- 记录每次调用的 token 用量与耗时，可设置每日调用上限。

## 4. 数据模型

```
source            站点/采集源
  id, name, start_url, fetch_mode, headers(JSONB),
  auth_type(none/cookie/form), auth_config(加密 JSON: cookies / 登录表单配置),
  storage_state(加密, 登录后的浏览器状态), rate_limit(每秒请求数), enabled, created_at

user              管理员账号（首期仅超级管理员，启动时按环境变量初始化）
  id, username, password_hash, is_superuser, created_at

rule              采集规则（版本化）
  id, source_id, version, config(JSONB, 见 3.1), is_active,
  origin(manual/visual/ai), note, created_at          -- 同一 source 仅一个 is_active

llm_provider      AI 模型配置（见 3.4）
  id, name, protocol(openai/anthropic), base_url, api_key(加密), model,
  params(JSONB: max_tokens/temperature/timeout), supports_tools, supports_vision,
  is_default, enabled

ai_generation     AI 生成记录
  id, source_id, provider_id, url, user_hint, status(success/failed),
  rounds(修正轮数), result_rule_id, prompt_tokens, completion_tokens,
  duration_ms, error, created_at

system_setting    全局设置（key/value），如 ai.enabled、ai.daily_limit、ai.auto_repair

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
2. **规则配置**：AI 生成（输入网址 + 可选说明 → 查看规则、试跑结果、AI 说明 → 确认保存）/ 可视化选取器 / JSON 编辑器，三种方式产出同一种规则。
3. **拍品列表**：按站点/状态/价格区间/结束时间筛选，关键词搜索，排序，CSV/Excel 导出。
4. **拍品详情**：基本信息 + 价格历史折线图（ECharts）。
5. **采集记录**：每次运行的状态、数量、耗时、错误日志。
6. **系统设置**：AI 总开关、模型配置（增删改、测试连接、设默认）、每日调用上限、AI 调用记录与 token 统计。

## 7. 稳定性与合规

- 限速：同一站点同时只允许一个采集任务（创建任务与调度时检查），任务内按站点 rate_limit 控制请求间隔（默认 1 req/s）；随机 UA；可选代理池（站点级开关，每次采集随机选择）。
- 重试：单个请求失败自动重试 3 次（指数退避）；首页抽取不到数据时在采集日志中保存页面片段便于排查。
- 登录态（站点级配置，平台不固定，按需选择）：
  - `none`：无需登录；
  - `cookie`：粘贴浏览器中的 Cookie（支持 `a=b; c=d` 字符串或浏览器插件导出的 JSON 数组），适用于有验证码/短信登录的平台；
  - `form`：配置登录页 URL、账号、密码及输入框/按钮选择器，由 Playwright 自动登录，保存 storage_state 复用；
    检测到登录失效（配置的"已登录标志"元素不存在或跳转到登录页）时自动重新登录一次。
  - 凭据全部加密存储，接口不回显明文。
- 规则失效检测：连续 N 次采集失败 → 失效（broken）并通过企业微信/钉钉/飞书/通用 Webhook 告警；关键字段空值率过高 → 异常（warning）；AI 已开启且允许自动修复时，自动生成新规则并直接启用。
- 合规：尊重目标站 robots 与服务条款，仅采集公开信息，控制频率。

## 8. 目录结构

```
collect/
├── backend/
│   ├── app/
│   │   ├── api/                # FastAPI 路由：auth / sources(含预览、快照、嗅探) / lots(含导出) / runs / ai / settings / templates
│   │   ├── models/entities.py  # SQLAlchemy 模型
│   │   ├── schemas/            # rule.py 规则 Schema（校验 AI 与人工规则）、api.py 接口模型
│   │   ├── crawler/
│   │   │   ├── fetchers/       # static(httpx，含 api 模式) / browser(Playwright，含登录、截图、XHR 捕获)
│   │   │   ├── extractor.py    # CSS / XPath / 正则 / JSONPath 抽取，详情页抽取
│   │   │   ├── transforms.py   # 金额、时间、状态等转换
│   │   │   ├── pipeline.py     # 标准化、去重 upsert、价格快照
│   │   │   ├── runner.py       # 采集执行（翻页、重试、详情页、盯价、日志）
│   │   │   ├── health.py       # 规则健康检查
│   │   │   ├── sniff.py        # 接口嗅探与字段猜测
│   │   │   └── snapshot.py     # 可视化选取器的页面快照
│   │   ├── ai/
│   │   │   ├── providers/      # base.py / openai_provider.py / anthropic_provider.py
│   │   │   ├── html_compress.py
│   │   │   ├── prompts.py
│   │   │   ├── rule_generator.py  # 生成 → 校验 → 试跑 → 修正循环
│   │   │   ├── service.py      # 模型选择、开关、每日上限、调用记录
│   │   │   └── repair.py       # 规则失效自动修复
│   │   ├── notify.py           # 告警 Webhook
│   │   ├── scheduler.py        # 定时调度（独立进程或随 API 启动）
│   │   ├── tasks.py / worker.py  # 任务投递 / arq worker
│   │   └── main.py
│   ├── alembic/                # 数据库迁移
│   └── tests/                  # 单元 + 集成测试（本地模拟拍卖站、模拟代理、假模型）
├── frontend/                   # Vue3 + Vite + Element Plus + ECharts
├── docker-compose.yml          # web / api / worker / scheduler / postgres / redis
└── docs/
```

## 9. 迭代计划（全部完成）

| 阶段 | 内容 | 状态 |
| --- | --- | --- |
| M1 MVP | 站点 CRUD、JSON 规则、static+browser 抓取、三种登录方式、手动触发、拍品列表 | ✅ |
| M1.5 AI | 模型配置（openai/anthropic 协议）、AI 开关与每日上限、AI 生成规则 + 试跑 + 自我修正、AI 一键添加站点 | ✅ |
| M2 | 可视化选取器、规则试跑、翻页、定时调度、采集日志 | ✅ |
| M3 | 价格历史与曲线、导出 Excel/CSV、规则失效检测与告警通知 | ✅ |
| M4 | 详情页、API 模式与接口嗅探、代理池与随机 UA、规则模板、盯价任务、AI 自动修复失效规则、截图多模态辅助 | ✅ |

## 10. 已确认决策

| 问题 | 决策 |
| --- | --- |
| 目标平台 | 不固定；部分需登录 → 站点级支持 none / cookie / form 三种登录方式 |
| 技术栈 | 后端 Python + FastAPI，前端 Vue3 + Element Plus |
| 部署 | 单机 docker-compose；站点数与采集频率均可动态配置 |
| 账号权限 | 首期仅超级管理员账号（环境变量初始化，JWT 登录），后续再扩展 |
| AI 模型 | 只实现 OpenAI 协议与 Anthropic 协议两种 Provider（覆盖 Claude、DeepSeek、Gemini 等）；暂不支持本地模型 |
| AI 审核 | 不设审核环节，AI 生成/修复的规则试跑通过即启用，依赖规则版本回滚兜底 |
