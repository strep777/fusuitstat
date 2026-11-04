# 弈星纪元兽装工作室官网

弈星纪元（上海）文化发展有限公司的兽装工作室官网，正式域名 `starium.cn`。前台是面向客户的展示、委托、商城与服务预约入口；后台用于内容、商品、订单、档期与审批运营。

- 前台：Vite + Vue 3 + TypeScript + Tailwind CSS 4
- 后台：独立入口 `admin.html`，与前台共用构建工具、设计 token 与 API 客户端
- 后端：FastAPI + SQLAlchemy 2 + Alembic + SQLite，模块化单体
- 边界防护：请求守卫中间件（限额、并发闸门、请求体上限、UA/蜜罐、匿名缓存）+ 反向代理
- 浏览器测试：Playwright（真实后端 + 真实前端 + 沙盒短信/支付）

业务规则、字段口径与验收标准以 `文档/` 为唯一来源；开始开发前先读 `文档/00_阅读入口与项目总纲.md`。

## 已实现的功能

### 前台

- **首页**：大图轮播（3 张、6 秒自动、可暂停与键盘操作）+ 极简说明与四个入口 + 最近完成；不再堆叠系列、流程、商品与 FAQ。
- **公开估价**：`/estimate` 无需登录，按系列起价、风格系数与加项实时显示参考区间，提交称呼、联系方式、角色说明与隐私同意；后台「估价请求」可标记已联系/关闭，不生成报价或订单。
- **委托**：委托指南、档期与名额、合同与条款、在线询价；公开估价 → 登录询价 → 报价版本 → 确认 → 合同 → 制作 → 交付。
- **作品与角色汤**：作品集与详情；`/characters` 角色汤展示原创角色、领养状态、授权与价格，保留不等于成交。
- **合并商城**：`/shop` 统一收纳现货、配件、道具与周边展示，支持分类、关键词与分页；道具/配件现货可购买，限量结构件先申请；周边只展示，周边卡片与详情不显示价格与加购。
- **保养服务**：`/wash` 展示单头、半装、全装与加急套餐；登录后选择套餐、期望日期（北京时间今天 +2 至 +60 天）、到店取回或寄回、备注并提交预约。预约不收款、不锁库存，工作室确认毛面与档期后给出报价和购买窗口。
- **旧入口兼容**：`/props` 重定向到 `/shop?category=props`，`/merch` 重定向到 `/shop?category=merch`；不维护第二份目录。
- **内容**：公告、常见问题、关于、联系、养护指南、法务页面（条款、隐私、退换、积分）。
- **账户**：手机号验证码登录、订单、预约申请、积分（星绒币）、反馈、售后、通知。
- **体验**：中英双语路由（`/en` 前缀，英文内容按运营发布情况回退）、深浅色主题、减弱动效、移动端与微信 WebView 适配。
- **界面 v5「White Atelier」**：白色为主、暖灰分区、陶土棕点缀；1px 细线、柔影、大留白、圆角图片；所有按钮无边框，靠底色与字重区分层级；首页采用可暂停的大图轮播，后台共用同一套克制视觉语言。

### 后台（`/admin.html`）

内容与页面、媒体、商品与 SKU、角色汤、档期与申请、委托与报价、订单、支付、退款与对账、反馈、积分、用户、员工与权限、站点设置、审计日志、后台任务。后台采用白色侧栏 + 白色工作区 + 无边框胶囊导航 + 1px 数据表；商品表单可维护类型、分类、服务类型与审批方式；「服务与掉落申请」页审批保养预约、掉落与配件申请。

### 防爬与高负载

- 请求守卫：分档令牌桶（全局/读/搜索/写/后台）、并发信号量（默认 128）与 80ms 排队、JSON 1MiB / multipart 32MiB 上限、UA 黑名单与 `/api/v1/_hp` 蜜罐、匿名公开 GET 15s 缓存。
- 后台可选 IP 允许清单；生产强制启用 Host 校验、HSTS、CSP 与爬虫拦截；IP 只存 HMAC 哈希。
- 静态 `robots.txt`、`sitemap.xml`、`.well-known/security.txt`；API 返回 `X-Robots-Tag: noindex` 与 `Cache-Control: no-store`。
- 容量边界见 `文档/19`：默认配置不承诺 50 并发容量 PASS，需在目标环境压测并记录；SQLite 单机仍是商业容量上限。

## 目录

```text
web/                  前台 + 后台两个构建入口
  src/app/            启动、路由、布局、站点外壳
  src/modules/        按领域拆分的 pages / components / api / stores / types
  src/shared/         无业务含义的 UI、HTTP、格式化、i18n、品牌 manifest
  tests/              Vitest 组件与页面测试
server/
  app/platform/       设置、数据库、UnitOfWork、时钟、ID、outbox、错误、安全、审计
  app/modules/        领域模块（catalog / commerce / commissions / content / identity / ...）
  app/application/    跨模块工作流
  app/adapters/       短信、邮件、存储、支付适配器
  migrations/         Alembic 迁移（当前 head = 0012）
  tests/              pytest 单元、集成与契约测试
e2e/                  Playwright 浏览器关键路径
contracts/            导出的 openapi.json
deploy/               Nginx / systemd / 备份脚本
scripts/              素材派生、冒烟脚本与开发辅助
素材/                 品牌原图（只读，不修改、不移动）
文档/                 需求、路由、数据、接口、测试与交付记录
```

## 本地启动

环境：Windows 10/11 或 Linux/macOS；Python 3.13、[uv](https://docs.astral.sh/uv/)、Node.js 20+、pnpm。

### 一键启动（Windows）

在仓库根目录运行：

```cmd
start.bat --no-browser
```

脚本会按需安装依赖、建库、写入演示数据，并同时启动后端 `127.0.0.1:8000` 与前端 `127.0.0.1:5173`。重复运行会复用已在运行的服务；`start.bat stop` 释放两个端口；`start.bat --help` 查看全部参数。Linux/macOS 使用 `bash start.sh`。

### 手动启动

后端：

```bash
cd server
uv sync
uv run python -m app.cli init-db          # 建库 + 迁移到 head
uv run python -m app.cli seed-demo        # 演示内容与商品（仅开发环境）
uv run python -m app.cli create-owner --login owner --name "工作室负责人"
uv run uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd web
pnpm install
pnpm dev        # http://127.0.0.1:5173，/api 代理到 127.0.0.1:8000
pnpm build      # 类型检查 + 前台/后台两个构建包
```

生产构建产物在 `web/dist/`；后台入口是 `/admin.html`。开发环境的短信验证码与支付走沙盒适配器：验证码写入 `server/var/storage/sandbox/sms.log`，登录接口返回 `dev_hint` 便于联调；生产环境不存在万能码、测试白名单或验证码回显。

开发环境未注入 `SESSION_SECRET` 等密钥时，首次使用会在 `server/var/.dev-secrets.json` 生成并保存本机密钥（0600、已被 git 忽略），使 uvicorn 重启后仍能解密演示库中的 MFA/PII 密文；预发布与生产必须显式注入，缺失即拒绝启动。

## 演示数据

`seed-demo` 幂等写入 21 条内容、4 个系列、13 个商品与 14 个 SKU，包括：

- 保养服务：单头 ¥168、半装 ¥268、全装 ¥398、加急 ¥280（均为预约制）
- 商城：道具（发光角饰 ¥128、可动耳饰 ¥198、尾巴支撑架 ¥158）、配件与周边展示（徽章、亚克力挂件、帆布托特包，不售卖）
- 原有：尾巴配件、角色商品、头部支撑内衬

演示商品标题带「（演示内容）」标记，商品卡以「演示」徽章展示；正式上线前由运营在后台替换为真实商品、图片、价格与库存。

## 验证

```bash
# 后端（全量）
cd server
uv run pytest -q
uv run ruff check app tests migrations

# 前端
cd web
pnpm typecheck
pnpm test
pnpm build

# 词条与界面文案门禁（仓库根目录）
python scripts/check_i18n.py
python scripts/check_font_license.py
python scripts/security_smoke.py --base-url http://127.0.0.1:8000 --path /api/v1/products --concurrency 8 --duration 15

# 浏览器关键路径（需服务已启动；本机无 Playwright 浏览器时用系统 Edge）
cd web
$env:E2E_BROWSER_CHANNEL="msedge"
pnpm exec playwright test --config ../e2e/playwright.config.ts
```

`e2e/README.md` 记录用例与服务端准备方式；未安装浏览器或服务未启动时不得把结果标记为 PASS。

## 关键业务规则

- 金额一律整数分（`_cents`），币种显式；价格、库存、权限、支付与积分由服务端裁定。
- 商品售罄仍可浏览详情；库存状态用文字徽章表达，不靠颜色单独区分。
- 保养服务预约写入 `stock_applications`（`kind=cleaning`），保存期望日期与返还方式；预约不生成付款项、不占库存，审批通过后才有购买窗口。
- 需要审批的商品（掉落、限量道具、保养服务）先申请，后台通过后 7 天内下单；拒绝或过期不可购买。
- 支付成功只认已验签通知或服务端主动查单；返回页、截图与前端回调不入账。
- 功能开关默认关闭；未启用能力入口隐藏且后端返回 `FEATURE_DISABLED`，不生成伪成功页。
- 手机号 HMAC 查找 + 加密保存；验证码只存 HMAC；会话只存哈希；写请求校验 CSRF 与 Origin。

## 设计规范

- 设计语言 v5「White Atelier」：白色 `#FFFFFF`、浅灰 `#F5F4F1`、暖墨 `#1F1D1A`、陶土棕 `#A65C36`、鼠尾草 `#63796B`、灰紫 `#887A96`、雾蓝 `#7890A3`、玫瑰 `#A55F68`、沙金 `#EFD8BC`；1px 细线、柔影、6–36px 圆角、无边框按钮；首页轮播 6 秒自动、悬停/聚焦暂停、`prefers-reduced-motion` 关闭自动。
- 1px 细线或纯柔影分隔；6–36px 圆角；标题 500 字重、`letter-spacing:-0.02em`；正文 16px / 1.7；按钮胶囊形、无边框，仅用底色与柔影变化反馈。
- 字体只随站分发 MIT 许可的 **Hack**（拉丁/数字），中文使用系统无衬线回退、不打包；禁止 Google Fonts 与远程 `@import`。详见 `文档/18`。
- 页面统一 loading / empty / error / forbidden / not-found 状态；表单覆盖 dirty、saving、saved、validation-error、session-expired。
- 文案先结论后条件，短句不超过 14 字，说明不超过 2 行；不使用空泛形容词。
- 品牌素材只通过 `web/src/shared/lib/brand-assets.ts` 引用：加载态 77_双曜弈兽、积分 62_弈环双尾、Logo 35_逐星绒龙；深色底用白色版 Logo。

## 文档索引

| 文档 | 内容 |
|---|---|
| `文档/00_阅读入口与项目总纲.md` | 阅读顺序、规则优先级、范围与版本 |
| `文档/02_软件需求规格说明书.md` | FR-001–026 与验收口径 |
| `文档/03_站点路由与页面功能.md` | 公开、账户与后台路由及页面状态 |
| `文档/04_技术选型与架构设计.md` | 模块边界、目录与技术决策 |
| `文档/05_数据模型与一致性约束.md` | 表结构、约束与事务不变量 |
| `文档/06_API接口契约.md` | HTTP 契约、错误码与预约字段 |
| `文档/08_商城订单支付与退款.md` | 商品分类、订单、支付、退款与保养服务流程 |
| `文档/11_界面交互与素材规范.md` | 设计方向 v5、组件状态与素材引用 |
| `文档/13_测试计划与验收用例.md` | 分层测试与 TC 用例 |
| `文档/16_需求追踪矩阵.md` | FR 与页面、接口、测试的对应 |
| `文档/17_交付检查与后续记录.md` | 每轮交付记录、证据与未验证项 |
| `文档/18_字体与视觉资产许可.md` | MIT 字体、中文回退与许可检查 |
| `文档/19_防爬虫与高并发安全设计.md` | 请求守卫、限额、并发、缓存与容量边界 |
| `文档/功能确认清单-已确认.md` | C01–C33 业务决定与 N01–N18 待补项 |

## 工程约束

- 模块只拥有自己的数据；跨模块只通过 `server/app/modules/contracts.py` 的 DTO 与端口，由 application workflow 编排。
- 同库强一致动作在一个 UnitOfWork 内完成；金额/库存争用用条件 UPDATE，不使用 SQLite 不支持的 `SELECT FOR UPDATE`。
- 创建订单/支付/退款、接受报价、反馈提交、积分调整必须带幂等键；outbox 消费按 `event_id` + 消费者名去重。
- 上传仅 JPEG/PNG/WebP，≤3 张、单张 ≤10MiB、边长 ≤8192、像素总量 ≤4000 万；安全解码后重新编码并移除 EXIF。
- 不编造资历、客户评价、竞品数据或未核准交期；参考站文案、Logo、人物照片不得下载使用。
- 不删测试、不关闭类型/签名/权限校验来让测试通过；沙盒适配器必须明确标记。

## 常见问题

**`uv` 缓存目录不可写**：设置 `UV_CACHE_DIR` 到仓库内（`.uv-cache/` 已忽略）。

**`pnpm test` 在无 TTY 环境中止**：直接运行 `node node_modules/vitest/vitest.mjs run`。

**端口被占用**：`start.bat stop` 会按 8000/5173 结束本项目进程；被其他程序占用时请手动确认。

**浏览器测试没有可用浏览器**：安装 Playwright Chromium，或设置 `E2E_BROWSER_CHANNEL=msedge` 使用系统 Edge（近似验证，最终以 CI 目标浏览器为准）。

**旧数据库缺列**：执行 `uv run python -m app.cli init-db` 应用迁移；不要手工改表。

## 本地回归检查

在仓库根目录使用 CMD，进入 `server` 运行 `uv run mypy app` 和 `uv run pytest --durations=5`。进入 `web` 运行 `pnpm lint && pnpm test && pnpm build`。估价配置变动后，在后端目录运行 `uv run python -m app.cli export-openapi`，再从仓库根目录运行 `python scripts/check_frontend_contract.py` 核对前端调用。
