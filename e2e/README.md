# 端到端浏览器测试

覆盖真实浏览器关键路径（04/13 文档）：公开浏览、筛选刷新恢复、登录、询价、
结算、支付结果核对、后台登录与权限边界，并包含桌面与手机两种视口、无数据态、
接口错误态与过期会话。

## 与单元/集成测试的分工

| 层级 | 位置 | 覆盖内容 |
|---|---|---|
| 领域单元 | `server/tests/unit` | 状态机、金额、积分、期限与权限规则 |
| 接口集成 | `server/tests/integration` | 真实 SQLite 临时文件、跨模块事务与并发语义 |
| 组件 | `web/tests` | 前端状态分支、表单校验、query 同步 |
| 浏览器（本目录） | `e2e/` | 真实渲染、真实交互、真实渠道沙盒 |

## 运行前提

Playwright 配置会**自己准备后端数据**：在 `e2e/.e2e-data/`（已忽略，不入库）里
清空并重新执行 `init-db` + `seed-demo`，再启动 uvicorn 与 Vite。这样每轮 journey
都从同一份干净数据开始，也不会消耗开发者自己 `server/var` 里的演示库存——
浏览器 journeys 会真实下单与扣库存，跑在开发者库上多跑几轮就会把库存卖光，
表现为「加购」之类的用例莫名失败（那是数据被消耗，不是产品坏了）。

只想手动起服务时（`E2E_NO_WEBSERVER=1`），按下面前两步准备即可：

1. 后端已建库并写入演示数据：

```bash
cd server
uv run python -m app.cli init-db
uv run python -m app.cli seed-demo
uv run uvicorn app.main:app --port 8000
```

2. 前端开发服务器（`/api` 代理到 8000）：

```bash
cd web && pnpm dev
```

3. 浏览器测试依赖 Playwright，按需安装（不写入 `package.json` 以保持前端依赖最小）：

```bash
pnpm dlx playwright@latest install --with-deps chromium
pnpm dlx playwright@latest test --config e2e/playwright.config.ts
```

本机无法下载 Playwright 自带 Chromium 时，可用系统浏览器做**近似验证**：
`$env:E2E_BROWSER_CHANNEL="msedge"`（CI 仍用自带 Chromium，最终以 CI 为准）。

## 用例对应关系

| 文件 | 覆盖的测试 ID |
|---|---|
| `journeys/public-browsing.spec.ts` | 界面与内容验收（直接打开、刷新、返回、窄屏、无数据、接口错误） |
| `journeys/commission-flow.spec.ts` | TC-C01、TC-C02 |
| `journeys/shop-checkout.spec.ts` | TC-O01、TC-P01、TC-P06 |
| `journeys/auth-security.spec.ts` | TC-A02、TC-A03、TC-A04、TC-A06 |
| `journeys/admin-permissions.spec.ts` | TC-A05 |

**未执行说明**：本目录的用例需要真实浏览器与运行中的服务；在未安装 Playwright
或未启动服务时不会被标记为通过（13 文档：BLOCKED 不算 PASS）。
