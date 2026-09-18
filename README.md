# 兽装工作室商业官网

弈星纪元兽装工作室官网（`starium.cn`）。前台与运营后台使用 Vite + Vue 3 + TypeScript + Tailwind CSS 4；后端使用 FastAPI + SQLAlchemy 2 + Alembic + SQLite，按模块化单体组织。

- 需求与规则唯一来源：`文档/`（进入前先读 `文档/00_阅读入口与项目总纲.md`）
- 业务决定：`文档/功能确认清单-已确认.md`（C01–C30）
- 模块边界与目录约束：`文档/04_技术选型与架构设计.md`
- 执行手册与中断续做约定：`文档/15_DeepSeek执行手册.md`

## 目录

```text
web/          前台 + 后台两个构建入口（src/modules/<domain>）
server/       FastAPI 模块化单体（app/modules/<domain>）
contracts/    由 FastAPI 导出的 openapi.json
e2e/          浏览器关键路径测试
deploy/       Nginx/systemd/备份脚本
scripts/      素材派生、开发辅助
素材/          品牌原图（只读，不修改、不移动）
文档/          规格与交付记录
```

## 本地启动

后端（Python 3.13 + uv）：

```bash
cd server
uv sync
uv run python -m app.cli init-db          # 建库 + 迁移 + 种子数据
uv run python -m app.cli seed-demo        # 演示内容（开发环境）
uv run uvicorn app.main:app --reload --port 8000
```

前端：

```bash
cd web
pnpm install
pnpm dev        # http://127.0.0.1:5173 ，/api 代理到 127.0.0.1:8000
pnpm build      # 类型检查 + 前台与后台两个构建包
```

开发环境默认启用沙盒适配器：短信验证码与支付渠道写入 `storage/` 与数据库，不调用任何真实服务商。生产环境必须显式配置真实适配器，否则启动即拒绝（fail closed）。

## 验证

```bash
# 后端
cd server
uv run pytest tests/unit/test_commerce_rules.py -q -p no:cacheprovider -o addopts=""
uv run ruff check app tests migrations

# 端到端联调（登录 → 上传 → 询价 → 购物车 → 结算 → 订单 → 支付拒绝 → 后台权限）
uv run python ../scripts/smoke_journey.py

# 前端
cd web
node node_modules/vue-tsc/bin/vue-tsc.js --noEmit -p tsconfig.app.json   # 类型检查
node node_modules/vitest/vitest.mjs run                                  # 组件测试
node node_modules/vite/bin/vite.js build                                 # 前台 + 后台构建

# 浏览器关键路径（需先安装 Playwright，见 e2e/README.md）
pnpm dlx playwright@latest test --config e2e/playwright.config.ts
```

Windows 本机注意：

- `uv` 缓存目录不可写时设置 `UV_CACHE_DIR` 到仓库内（`.uv-cache/` 已忽略）。
- `pnpm test` 会先做依赖校验；若因无 TTY 中止，直接调用 `node node_modules/vitest/vitest.mjs run`。
- 整目录 `uv run pytest tests` 若长时间无输出，通常是上一轮中断留下的 python 进程仍占用
  同一个 SQLite 文件；先 `Get-Process pytest*, python* | Stop-Process -Force` 再按文件运行。

## 安全与合规约束（不可绕过）

- 金额一律整数分；价格、库存、权限、支付与积分由服务端裁定，前端只提交选择。
- 支付成功只认已验签通知或服务端主动查单；返回页与前端回调不入账。
- 手机号以 HMAC 查找值索引、加密保存；验证码只存 HMAC，不写日志。
- 私密设定图与量体数据不进公开 CDN、不预渲染、不写入公开日志。
- 未确认或未达上线门槛的功能开关保持关闭，不生成伪成功页。
