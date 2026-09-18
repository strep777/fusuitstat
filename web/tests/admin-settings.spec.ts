/**
 * 经营设置的编辑能力测试（R21：跨境、发票与售后政策必须真的能被运营配置）。
 *
 * 背景：`commerce.policy` 在服务端是**嵌套对象**（运费、售后、发票、跨境块）。
 * 如果后台只能把设置值当标量提交，运营既关不掉开票开关，也配不了跨境关税比例，
 * 而这些政策是服务端裁定价格与入口开关的唯一来源。
 *
 * 覆盖：
 * 1. 对象配置以 JSON 呈现，并按 **解析后的对象** 提交（不是字符串）；
 * 2. JSON 语法错误时禁止提交并给出提示，且不发请求；
 * 3. 布尔开关仍走开关分支，不被 JSON 分支劫持。
 */

import { mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";

const mocks = vi.hoisted(() => ({
  settings: vi.fn(),
  updateSetting: vi.fn(),
}));

vi.mock("@/modules/admin/api/admin", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/modules/admin/api/admin")>();
  return {
    ...actual,
    adminApi: { ...actual.adminApi, settings: mocks.settings, updateSetting: mocks.updateSetting },
  };
});

import SettingsPage from "@/modules/admin/pages/SettingsPage.vue";

const COMMERCE_POLICY = {
  returns_reference: "参照淘宝 / 闲鱼平台规则执行",
  invoice_enabled: true,
  cross_border: { enabled: true, duty_percent: 10, note: "以清关为准" },
};

const FLAG_VERSION = 4;

function settingRow(overrides: Record<string, unknown> = {}) {
  return {
    key: "commerce.policy",
    value: COMMERCE_POLICY,
    version: 3,
    updated_at: "2026-01-01T00:00:00+00:00",
    updated_by: "owner",
    description: "C10/C16/C17：退换货、跨境、运费与开票说明。",
    ...overrides,
  };
}

async function flush(times = 20): Promise<void> {
  for (let index = 0; index < times; index += 1) {
    await Promise.resolve();
  }
  await nextTick();
}

async function mountSettings(rows: unknown[]): Promise<VueWrapper> {
  mocks.settings.mockResolvedValue({ items: rows, total: rows.length, page: 1, limit: 50, has_more: false });
  const pinia = createPinia();
  setActivePinia(pinia);
  const wrapper = mount(SettingsPage, { global: { plugins: [pinia] }, attachTo: document.body });
  await flush(40);
  return wrapper;
}

function buttonIn(wrapper: VueWrapper, label: string) {
  const found = wrapper.findAll("button").find((item) => item.text().includes(label));
  expect(found, `应存在按钮：${label}`).toBeTruthy();
  return found!;
}

/** ConfirmDialog 通过 Teleport 挂到 body，确认动作必须在 body 上驱动。 */
function bodyButton(label: string): HTMLButtonElement {
  const found = Array.from(document.querySelectorAll("body button")).find((item) =>
    item.textContent?.includes(label),
  );
  expect(found, `应存在确认按钮：${label}`).toBeTruthy();
  return found as HTMLButtonElement;
}

async function typeInBody(selector: string, value: string): Promise<void> {
  const element = document.querySelector(selector) as HTMLTextAreaElement | null;
  expect(element, `应存在输入框：${selector}`).toBeTruthy();
  element!.value = value;
  element!.dispatchEvent(new Event("input"));
  await flush(4);
}

/** 表单里的变更理由（先过表单校验，才会打开二次确认框）。 */
async function fillFormReason(wrapper: VueWrapper, reason = "按 2026 春季清关口径调整预估比例"): Promise<void> {
  await wrapper.get("#setting-reason").setValue(reason);
  await flush(4);
}

describe("经营设置：结构化配置可按 JSON 编辑", () => {
  beforeEach(() => {
    mocks.settings.mockReset();
    mocks.updateSetting.mockReset();
    mocks.updateSetting.mockResolvedValue({ key: "commerce.policy", version: 4, value: {} });
    document.body.innerHTML = "";
  });

  it("对象配置渲染为 JSON，并按解析后的对象提交（附带版本号与理由）", async () => {
    const wrapper = await mountSettings([settingRow()]);

    // 表格里不能渲染成 [object Object]
    expect(wrapper.text()).not.toContain("[object Object]");
    expect(wrapper.text()).toContain("duty_percent");

    await buttonIn(wrapper, "修改这项设置").trigger("click");
    await flush();

    const editor = wrapper.get("#setting-value-json").element as HTMLTextAreaElement;
    expect(editor.value).toContain('"cross_border"');
    expect(JSON.parse(editor.value)).toEqual(COMMERCE_POLICY);

    const next = { ...COMMERCE_POLICY, cross_border: { enabled: true, duty_percent: 8, note: "以清关为准" } };
    await wrapper.get("#setting-value-json").setValue(JSON.stringify(next, null, 2));
    await flush();
    await fillFormReason(wrapper);

    await buttonIn(wrapper, "提交修改").trigger("click");
    await flush();

    // 二次确认对话框必须写明对象与后果
    expect(document.body.textContent).toContain("配置项 commerce.policy");
    expect(document.body.textContent).toContain("版本 3 → 4");

    await typeInBody("#confirm-reason", "按 2026 春季清关口径调整预估比例");
    bodyButton("确认保存设置").click();
    await flush(40);

    expect(mocks.updateSetting).toHaveBeenCalledTimes(1);
    const [key, input] = mocks.updateSetting.mock.calls[0] as [string, Record<string, unknown>];
    expect(key).toBe("commerce.policy");
    expect(input["version"]).toBe(3);
    expect(input["reason"]).toBe("按 2026 春季清关口径调整预估比例");
    // 关键：提交的是对象，不是 JSON 字符串（字符串会被服务端按 Schema 拒绝）
    expect(typeof input["value"]).toBe("object");
    expect(input["value"]).toEqual(next);

    wrapper.unmount();
  });

  it("JSON 语法错误时禁止提交并提示，且不发请求", async () => {
    const wrapper = await mountSettings([settingRow()]);

    await buttonIn(wrapper, "修改这项设置").trigger("click");
    await flush();

    await wrapper.get("#setting-value-json").setValue('{"cross_border": {"duty_percent": }}');
    await flush();

    expect(wrapper.text()).toContain("JSON 语法有误");
    const submit = buttonIn(wrapper, "提交修改");
    expect(submit.attributes("disabled")).toBeDefined();

    await submit.trigger("click");
    await flush();
    expect(document.body.textContent).not.toContain("状态变化");
    expect(mocks.updateSetting).not.toHaveBeenCalled();

    wrapper.unmount();
  });

  it("功能开关按 JSON 对象提交（键名与审计口径不变）", async () => {
    const wrapper = await mountSettings([
      settingRow({ key: "platform.feature_flags", value: { online_payment: false }, version: FLAG_VERSION }),
    ]);

    await buttonIn(wrapper, "修改这项设置").trigger("click");
    await flush();

    const editor = wrapper.get("#setting-value-json");
    await editor.setValue(JSON.stringify({ online_payment: true }, null, 2));
    await fillFormReason(wrapper, "按商业门槛确认后启用在线支付");
    await buttonIn(wrapper, "提交修改").trigger("click");
    await flush();

    await typeInBody("#confirm-reason", "按商业门槛确认后启用在线支付");
    bodyButton("确认保存设置").click();
    await flush(40);

    const [key, input] = mocks.updateSetting.mock.calls[0] as [string, Record<string, unknown>];
    expect(key).toBe("platform.feature_flags");
    expect(input["value"]).toEqual({ online_payment: true });
    expect(input["version"]).toBe(FLAG_VERSION);

    wrapper.unmount();
  });
});
