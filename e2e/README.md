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
