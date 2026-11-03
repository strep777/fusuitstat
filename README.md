# 弈星纪元兽装工作室官网

弈星纪元（上海）文化发展有限公司的兽装工作室官网，正式域名 `starium.cn`。前台是面向客户的展示、委托、商城与服务预约入口；后台用于内容、商品、订单、档期与审批运营。

- 前台：Vite + Vue 3 + TypeScript + Tailwind CSS 4
- 后台：独立入口 `admin.html`，与前台共用构建工具、设计 token 与 API 客户端
- 后端：FastAPI + SQLAlchemy 2 + Alembic + SQLite，模块化单体
- 浏览器测试：Playwright（真实后端 + 真实前端 + 沙盒短信/支付）

业务规则、字段口径与验收标准以 `文档/` 为唯一来源；开始开发前先读 `文档/00_阅读入口与项目总纲.md`。

## 已实现的功能

### 前台

- **首页**：品牌主张、当前档期、三条业务线入口、代表作品、系列对比、委托流程、商城商品、公告与常见问题。
- **委托**：委托指南、档期与名额、合同与条款、在线询价；询价 → 报价版本 → 确认 → 合同 → 制作 → 交付。
- **作品与角色**：作品集与详情、角色池与角色详情；公开素材按授权状态展示。
- **商城**：现货、配件、道具、周边、服务五类目录，支持分类、类型、关键词、系列、币种与分页；商品详情、SKU、购物车、结算、支付结果、售后与退款。
- **洗毛与护理**：`/wash` 展示单头、半装、全装与加急套餐；登录后选择套餐、期望日期（北京时间今天 +2 至 +60 天）、到店取回或寄回、备注并提交预约。预约不收款、不锁库存，工作室确认毛面与档期后给出报价和购买窗口。
- **道具工坊**：`/props` 与 `/shop?category=props` 共用目录与库存；现货直购，限量或定制结构件先申请审批。
- **周边商店**：`/merch` 与 `/shop?category=merch` 共用目录；徽章、挂件、托特包等小件现货。
- **内容**：公告、常见问题、关于、联系、养护指南、法务页面（条款、隐私、退换、积分）。
- **账户**：手机号验证码登录、订单、预约申请、积分（星绒币）、反馈、售后、通知。
- **体验**：中英双语路由（`/en` 前缀，英文内容按运营发布情况回退）、深浅色主题、减弱动效、移动端与微信 WebView 适配。

### 后台（`/admin.html`）

内容与页面、媒体、商品与 SKU、角色、档期与申请、委托与报价、订单、支付、退款与对账、反馈、积分、用户、员工与权限、站点设置、审计日志、后台任务。商品表单可维护类型、分类、服务类型与审批方式；「服务与掉落申请」页审批洗毛预约、掉落与配件申请。

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
  migrations/         Alembic 迁移（当前 head = 0011）
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

## 演示数据

`seed-demo` 幂等写入 21 条内容、4 个系列、13 个商品与 14 个 SKU，包括：

- 洗毛：单头 ¥168、半装 ¥268、全装 ¥398、加急 ¥280（均为预约制）
- 道具：发光角饰 ¥128、可动耳饰 ¥198、尾巴支撑架 ¥158（限量件需审批）
- 周边：星绒徽章 ¥38、亚克力挂件 ¥48、帆布托特包 ¥88
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
python check_i18n.py

# 浏览器关键路径（需服务已启动；本机无 Playwright 浏览器时用系统 Edge）
cd web
$env:E2E_BROWSER_CHANNEL="msedge"
pnpm exec playwright test --config ../e2e/playwright.config.ts
```

`e2e/README.md` 记录用例与服务端准备方式；未安装浏览器或服务未启动时不得把结果标记为 PASS。

## 关键业务规则

- 金额一律整数分（`_cents`），币种显式；价格、库存、权限、支付与积分由服务端裁定。
- 商品售罄仍可浏览详情；库存状态用文字徽章表达，不靠颜色单独区分。
- 洗毛预约写入 `stock_applications`（`kind=cleaning`），保存期望日期与返还方式；预约不生成付款项、不占库存，审批通过后才有购买窗口。
- 需要审批的商品（掉落、限量道具、洗毛）先申请，后台通过后 7 天内下单；拒绝或过期不可购买。
- 支付成功只认已验签通知或服务端主动查单；返回页、截图与前端回调不入账。
- 功能开关默认关闭；未启用能力入口隐藏且后端返回 `FEATURE_DISABLED`，不生成伪成功页。
- 手机号 HMAC 查找 + 加密保存；验证码只存 HMAC；会话只存哈希；写请求校验 CSRF 与 Origin。

## 设计规范

- 奶油底 `#FFF8F0`，李子墨正文 `#2B2033`，珊瑚红主色 `#D93A24`（白字对比 ≥ 4.5:1），薄荷 `#0C8F7C`、葡萄 `#6742E8`、柠檬 `#F0A000` 作业务与状态强调。
- 全站圆润无衬线，标题 800 字重；正文 16px / 1.65；圆角 8–28px，按钮胶囊。
- 页面统一 loading / empty / error / forbidden / not-found 状态；表单覆盖 dirty、saving、saved、validation-error、session-expired。
- 文案先结论后条件，短句不超过 14 字，说明不超过 2 行；不使用空泛形容词。
- 品牌素材只通过 `web/src/shared/lib/brand-assets.ts` 引用：加载态 77_双曜弈兽、积分 62_弈环双尾、Logo 35_逐星绒龙；深色底用白色版 Logo。

## 文档索引

| 文档 | 内容 |
|---|---|
| `文档/00_阅读入口与项目总纲.md` | 阅读顺序、规则优先级、范围与版本 |
| `文档/02_软件需求规格说明书.md` | FR-001–025 与验收口径 |
| `文档/03_站点路由与页面功能.md` | 公开、账户与后台路由及页面状态 |
| `文档/04_技术选型与架构设计.md` | 模块边界、目录与技术决策 |
| `文档/05_数据模型与一致性约束.md` | 表结构、约束与事务不变量 |
| `文档/06_API接口契约.md` | HTTP 契约、错误码与预约字段 |
| `文档/08_商城订单支付与退款.md` | 商品分类、订单、支付、退款与洗毛流程 |
| `文档/11_界面交互与素材规范.md` | 设计方向 v2、组件状态与素材引用 |
| `文档/13_测试计划与验收用例.md` | 分层测试与 TC 用例 |
| `文档/16_需求追踪矩阵.md` | FR 与页面、接口、测试的对应 |
| `文档/17_交付检查与后续记录.md` | 每轮交付记录、证据与未验证项 |
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
