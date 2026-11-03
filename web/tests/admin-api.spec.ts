/**
 * 后台经营设置的响应形状回归。
 *
 * 真实后端 `GET /admin/settings` 返回裸数组；若前端只接受 `{ items }`，
 * `state.data.items` 会变成 undefined，页面会一直停在加载骨架。
 */
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  get: vi.fn(),
}));

vi.mock("@/shared/lib/http", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/shared/lib/http")>();
  return {
    ...actual,
    api: { ...actual.api, get: mocks.get },
  };
});

import { adminApi } from "@/modules/admin/api/admin";

const ROWS = [
  {
    key: "brand.profile",
    value: { brand_name: "弈星纪元" },
    version: 1,
    updated_at: "2026-11-04T00:00:00+00:00",
    updated_by: "owner",
  },
];

describe("adminApi.settings 响应归一化", () => {
  beforeEach(() => {
    mocks.get.mockReset();
  });

  it("裸数组包装成 { items }，页面不再读到 undefined", async () => {
    mocks.get.mockResolvedValue(ROWS);
    const payload = await adminApi.settings();
    expect(payload.items).toEqual(ROWS);
  });

  it("已分页的 { items } 原样透传", async () => {
    const page = { items: ROWS, total: 1, page: 1, limit: 50, has_more: false };
    mocks.get.mockResolvedValue(page);
    await expect(adminApi.settings()).resolves.toEqual(page);
  });
});
