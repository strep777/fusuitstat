import { expect, test } from "@playwright/test";

// 仅替换商品数据；路由、配置读取、组件渲染均使用本地应用。
test("英文商品详情：美元请求、登录返回地址、售罄提示与窄屏", async ({ page }) => {
  const errors: string[] = [];
  const currencies: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.route("**/api/v1/products/locale-test*", async (route) => {
    const currency = new URL(route.request().url()).searchParams.get("currency") ?? "CNY";
    currencies.push(currency);
    await route.fulfill({ json: {
      id: "locale-test", slug: "locale-test", title: "Test accessory", subtitle: "",
      kind: "stock_goods", category: "accessory", currency, price_from_cents: 2500,
      availability: "sold_out", requires_approval: false, images: [], tags: [],
      description_html: "", shipping_note: "Shipping confirmed at checkout",
      after_sales_note: "See the returns policy",
      skus: [{ id: "locale-sku", product_id: "locale-test", sku_code: "TEST", label: "Medium",
        currency, price_cents: 2500, compare_at_cents: null, attributes: { size: "M" },
        available: true, in_stock: false, low_stock: false, max_per_order: 1, weight_grams: 100 }],
    } });
  });
  await page.goto("/en/shop/locale-test");
  await expect(page.getByRole("heading", { name: "Test accessory", exact: true })).toBeVisible();
  await expect.poll(() => currencies.includes("USD")).toBe(true);
  await expect(page.getByText("All variants are sold out.", { exact: false })).toBeVisible();
  await expect(page.getByRole("button", { name: "Add to cart", exact: true })).toBeDisabled();
  const login = page.getByRole("link", { name: "Log in with your phone", exact: true });
  await expect(login).toHaveAttribute("href", /\/en\/login\?returnTo=%2Fen%2Fshop%2Flocale-test/);
  await expect(page.getByRole("link", { name: "Read returns and repair policies" })).toHaveAttribute("href", "/en/legal/returns");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBe(true);
  await page.reload();
  await expect(page.getByRole("heading", { name: "Test accessory", exact: true })).toBeVisible();
  await expect(page.locator("html")).toHaveAttribute("lang", "en-US");
  expect(errors).toEqual([]);
});
