/**
 * Playwright 配置（浏览器关键路径）。
 *
 * 依赖不在 web/package.json 中声明，避免把浏览器二进制装进前端生产依赖；
 * 使用方式见 e2e/README.md：`pnpm dlx playwright@latest test --config e2e/playwright.config.ts`
 */

import { defineConfig, devices } from "@playwright/test";
import path from "node:path";

const SITE_ORIGIN = process.env.E2E_SITE_ORIGIN ?? "http://127.0.0.1:5173";

// 配置文件由 Playwright 以 CJS 方式加载（见 e2e/tsconfig.json），因此用 __dirname 而不是 import.meta。
const E2E_ROOT = __dirname;

/**
 * e2e 专用数据目录。
 *
 * 浏览器 journeys 会真实下单、扣库存、发验证码；如果直接跑在开发者自己的
 * `server/var` 上，多跑几轮就会把演示库存卖光，之后「加购」这类用例会莫名其妙
 * 失败（那是数据被消耗，不是产品坏了）。这里给 e2e 一份**独立且每次重置**的数据：
 * 与 CI 的 `init-db` + `seed-demo` 行为一致，同时不再改动开发者的本地库。
 */
const E2E_DATA_DIR = process.env.E2E_DATA_DIR ?? path.join(E2E_ROOT, ".e2e-data");

/**
 * 浏览器通道。
 *
 * 默认使用 Playwright 自带的 Chromium（CI 里由 `playwright install chromium` 提供）。
 * 本机若无法下载浏览器，可以复用系统已装的 Chromium 内核浏览器：
 *   PowerShell: `$env:E2E_BROWSER_CHANNEL="msedge"; pnpm exec playwright test --config ../e2e/playwright.config.ts`
 * 可用值：msedge / chrome / chromium。设置后跑的是同一套用例与同一套断言，
 * 只是渲染内核来自系统浏览器，属于**近似验证**，不替代 CI 上的目标浏览器结果。
 */
const BROWSER_CHANNEL = process.env.E2E_BROWSER_CHANNEL?.trim();
const channel = BROWSER_CHANNEL ? { channel: BROWSER_CHANNEL as "msedge" | "chrome" | "chromium" } : {};

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: [["list"], ["html", { open: "never", outputFolder: "report" }]],
  use: {
    baseURL: SITE_ORIGIN,
    locale: "zh-CN",
    timezoneId: "Asia/Shanghai",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "off",
    ...channel,
  },
  projects: [
    { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "mobile-360", use: { ...devices["Pixel 7"], viewport: { width: 360, height: 780 } } },
    {
      // 目标机型包含近期微信 WebView（C23）；此处用移动端 Chromium 近似，
      // 真实微信 WebView 需在 I6 用真机补实验证。
      name: "wechat-webview-approx",
      use: { ...devices["Pixel 7"], viewport: { width: 393, height: 852 } },
    },
  ],
  webServer: process.env.E2E_NO_WEBSERVER
    ? undefined
    : [
        {
          // 先清空 e2e 数据目录再初始化并写入演示数据，最后启动后端：
          // 这样每轮 journey 都从同一份干净数据开始，用例结果可复现。
          command:
            "uv run python -c \"import os,shutil;shutil.rmtree(os.environ['DATA_DIR'], ignore_errors=True)\""
            + " && uv run python -m app.cli init-db"
            + " && uv run python -m app.cli seed-demo"
            + " && uv run uvicorn app.main:app --port 8000",
          cwd: "../server",
          url: "http://127.0.0.1:8000/readyz",
          reuseExistingServer: true,
          timeout: 180_000,
          env: {
            APP_ENV: "development",
            DATA_DIR: E2E_DATA_DIR,
            // 浏览器 journey 从同一个 127.0.0.1 发起数百次请求，全站令牌桶会把
            // 正常用例误判为刷接口。守卫本身由 server/tests/integration/
            // test_request_guard.py 单独覆盖；这里关闭的是 e2e 进程内的重复计数。
            SECURITY_GUARD_ENABLED: "false",
            // e2e 每次用例都要真实登录，几十次发送会撞上「同 IP 20 次/小时」的防刷限额。
            // 这里只放宽**测试环境**的限额数值；限流逻辑本身由
            // server/tests/integration/test_sms_rate_limit.py 在服务端固定验证，
            // 生产默认值不变（OTP_IP_HOURLY_LIMIT 默认 20）。
            OTP_IP_HOURLY_LIMIT: "1000",
            OTP_DAILY_BUDGET: "100000",
          },
        },
        {
          command: "pnpm dev",
          cwd: "../web",
          url: "http://127.0.0.1:5173",
          reuseExistingServer: true,
          timeout: 120_000,
        },
      ],
});
