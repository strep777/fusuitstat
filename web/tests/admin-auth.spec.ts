/**
 * 运营后台验收测试（TC-A05 相关）。
 *
 * 覆盖：
 * 1. 未登录访问后台页面 → 跳转 `/admin/login` 并保留不含敏感信息的目标路径；
 * 2. 权限码不足 → 菜单/入口不渲染，并说明缺少的权限码（界面隐藏不能代替授权）；
 * 3. 危险动作确认文案包含**具体对象、金额与后果**，不能只有泛用「确定」；
 * 4. MFA 两步流程状态机：口令 → 首次登记（只显示一次的登记信息）→ 验证 → 已登录；
 *    未完成第一步就提交验证码会被状态机拒绝。
 *
 * 说明：测试用 `adminApi` 的假实现替换网络层，不访问真实后端，也不伪造页面上未声明的数据。
 */

import { mount } from "@vue/test-utils";
import { createPinia, setActivePinia, type Pinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { defineComponent, h, nextTick, ref } from "vue";
import { createMemoryHistory } from "vue-router";

import { ApiError } from "@/shared/lib/http";
import {
  ADMIN_NAV,
  ADMIN_PERMISSIONS,
  filterAdminNav,
  useAdminAuthStore,
} from "@/modules/admin/stores/adminAuth";

const mocks = vi.hoisted(() => ({
  login: vi.fn(),
  mfa: vi.fn(),
  logout: vi.fn(),
  me: vi.fn(),
  dashboard: vi.fn(),
  content: vi.fn(),
  products: vi.fn(),
  quotes: vi.fn(),
  orders: vi.fn(),
  refunds: vi.fn(),
}));

vi.mock("@/modules/admin/api/admin", () => ({
  adminApi: mocks,
  /** 与真实实现保持一致的错误分类，避免测试断言与生产行为脱节。 */
  describeFailure: (error: unknown, subject: string) => {
    const status = (error as { status?: number })?.status ?? 0;
    if (status === 403) {
      return { kind: "forbidden", title: `无权访问${subject}`, description: "缺少权限码", retryHint: "联系管理员" };
    }
    return { kind: "error", title: `${subject}加载失败`, description: "请求失败", retryHint: "可以重试" };
  },
  actionErrorMessage: (error: unknown, fallback: string) => {
    const typed = error as { isVersionConflict?: boolean; message?: string; code?: string };
    if (typed?.isVersionConflict) {
      return { message: "内容已被他人更新，请刷新后重新提交。", versionConflict: true };
    }
    return { message: typed?.message ? `${typed.message}（错误码 ${typed.code ?? ""}）` : fallback, versionConflict: false };
  },
  authorizationSummary: () => "未登记",
  text: (value: unknown) => (value === null || value === undefined || value === "" ? "—" : String(value)),
}));

// 路由表引用了全部后台页面与外壳；用轻量替身避免为跳转测试渲染真实页面。
vi.mock("@/app/layouts/AdminLayout.vue", () => ({
  default: defineComponent({
    name: "AdminLayoutStub",
    setup(_props, { slots }) {
      return () => h("div", { "data-testid": "admin-layout" }, slots.default?.());
    },
  }),
}));

vi.mock("@/modules/admin/pages/DashboardPage.vue", () => ({
  default: defineComponent({ name: "DashboardStub", setup: () => () => h("p", "概览占位") }),
}));

vi.mock("@/modules/admin/pages/AdminNotFoundPage.vue", async () => {
  const vue = await import("vue");
  return {
    AdminPageHeader: vue.defineComponent({
      name: "AdminPageHeader",
      props: { title: { type: String, required: true }, description: { type: String, default: "" } },
      setup(props) {
        return () => vue.h("header", [vue.h("h1", props.title), vue.h("p", props.description)]);
      },
    }),
    InlineNotice: vue.defineComponent({
      name: "InlineNotice",
      props: { title: { type: String, required: true }, description: { type: String, default: "" } },
      setup(props) {
        return () => vue.h("div", { role: "note" }, [vue.h("p", props.title), vue.h("p", props.description)]);
      },
    }),
    FieldList: vue.defineComponent({ name: "FieldList", setup: () => () => vue.h("dl") }),
    ModuleCard: vue.defineComponent({
      name: "ModuleCard",
      setup(_props, { slots }) {
        return () => vue.h("section", slots.default?.());
      },
    }),
    default: vue.defineComponent({ name: "AdminNotFoundStub", setup: () => () => vue.h("div", "页面不存在") }),
  };
});

const permissionGateModule = await import("@/modules/admin/components/PermissionGate.vue");
const PermissionGate = permissionGateModule.default;
const ConfirmDialog = (await import("@/modules/admin/components/ConfirmDialog.vue")).default;
const { createAdminRouter, registerAdminGuards, safeReturnTo } = await import("@/app/admin-router");
const { adminNavFor } = await import("@/modules/admin/stores/adminAuth");

const STAFF_ID = "staff_test_1";

function mfaPayload(overrides: Record<string, unknown> = {}) {
  return {
    staff_id: STAFF_ID,
    display_name: "测试员工",
    login_name: "tester",
    permissions: [ADMIN_PERMISSIONS.dashboard],
    roles: ["ops"],
    session_expires_at: "2026-01-01T00:00:00Z",
    csrf_token: "csrf_test_token",
    mfa_enabled: true,
    ...overrides,
  };
}

function unauthorized(): ApiError {
  return new ApiError(401, {
    code: "AUTH_REQUIRED",
    message: "请先登录运营后台。",
    fields: {},
    trace_id: "trace_test",
  });
}

function useRouter() {
  // 每个用例新建路由实例并注册守卫，避免用例之间共享会话状态。
  const router = createAdminRouter(createMemoryHistory());
  registerAdminGuards(router);
  return router;
}

let pinia: Pinia;

beforeEach(() => {
  pinia = createPinia();
  setActivePinia(pinia);
  mocks.login.mockReset();
  mocks.mfa.mockReset();
  mocks.logout.mockReset();
  mocks.me.mockReset();
  mocks.dashboard.mockReset();
  mocks.me.mockRejectedValue(unauthorized());
});

describe("后台未登录跳转", () => {
  it("未登录访问深层后台路径时跳转到员工登录页并保留目标路径", async () => {
    const router = useRouter();
    await router.push("/admin/orders/order_123");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("admin-login");
    expect(router.currentRoute.value.query.returnTo).toBe("/admin/orders/order_123");
  });

  it("登录后可以进入概览页", async () => {
    mocks.me.mockResolvedValue({
      staff_id: STAFF_ID,
      display_name: "测试员工",
      login_name: "tester",
      permissions: [ADMIN_PERMISSIONS.dashboard],
      roles: ["ops"],
      session_expires_at: "2026-01-01T00:00:00Z",
      mfa_enabled: true,
    });

    const router = useRouter();
    await router.push("/admin/dashboard");
    await router.isReady();

    expect(router.currentRoute.value.name).toBe("admin-dashboard");
    const auth = useAdminAuthStore();
    expect(auth.isAuthenticated).toBe(true);
  });

  it("returnTo 只接受本站后台相对路径，避免开放重定向", () => {
    expect(safeReturnTo("/admin/refunds")).toBe("/admin/refunds");
    expect(safeReturnTo("https://evil.example.com")).toBe("/admin/dashboard");
    expect(safeReturnTo("//evil.example.com")).toBe("/admin/dashboard");
    expect(safeReturnTo("/login")).toBe("/admin/dashboard");
    expect(safeReturnTo("/admin/login?returnTo=/admin")).toBe("/admin/dashboard");
  });
});

describe("权限码不足时不渲染入口", () => {
  it("菜单过滤把无权限项归入 denied，而不是静默当成可用", () => {
    const result = filterAdminNav([ADMIN_PERMISSIONS.dashboard], true);
    expect(result.granted.map((item) => item.id)).toEqual(["dashboard"]);
    expect(result.denied.map((item) => item.id)).toContain("refunds");

    const refunds = adminNavFor(ADMIN_PERMISSIONS.refundRequest);
    expect(refunds?.label).toBe("退款");
    expect(result.denied.find((item) => item.id === "refunds")?.permission).toBe(ADMIN_PERMISSIONS.refundRequest);
    expect(ADMIN_NAV.length).toBe(result.granted.length + result.denied.length);
  });

  it("PermissionGate 在缺少权限码时隐藏入口并写明所需权限码", () => {
    const auth = useAdminAuthStore(pinia);
    auth.$patch({
      staff: { id: STAFF_ID, display_name: "测试员工", login_name: "tester" },
      permissions: [ADMIN_PERMISSIONS.dashboard],
    });

    const wrapper = mount(PermissionGate, {
      props: { code: ADMIN_PERMISSIONS.refundApprove, subject: "退款批准" },
      slots: { default: () => h("button", { type: "button" }, "批准退款") },
      global: { plugins: [pinia] },
    });

    expect(wrapper.find("button").exists()).toBe(false);
    const text = wrapper.text();
    expect(text).toContain(ADMIN_PERMISSIONS.refundApprove);
    expect(text).toContain("界面隐藏不能代替授权");
  });

  it("具备权限码时正常渲染入口", () => {
    const auth = useAdminAuthStore(pinia);
    auth.$patch({
      staff: { id: STAFF_ID, display_name: "测试员工", login_name: "tester" },
      permissions: [ADMIN_PERMISSIONS.refundApprove],
    });

    const wrapper = mount(PermissionGate, {
      props: { code: ADMIN_PERMISSIONS.refundApprove },
      slots: { default: () => h("button", { type: "button" }, "批准退款") },
      global: { plugins: [pinia] },
    });

    expect(wrapper.find("button").text()).toBe("批准退款");
  });
});

describe("危险动作确认文案", () => {
  /**
   * 对话框内容通过 ``<Teleport to="body">`` 渲染到 body，
   * 因此断言要读 document（而不是 wrapper），否则 DOMWrapper 为空。
   * 组件把弹层放在 body 是正确的实现：避免被祖先的 overflow / z-index 裁剪。
   */
  function mountDialog() {
    const open = ref(true);
    const Host = defineComponent({
      name: "ConfirmDialogHost",
      setup() {
        return () =>
          h(ConfirmDialog, {
            modelValue: open.value,
            "onUpdate:modelValue": (value: boolean) => {
              open.value = value;
            },
            title: "批准退款（原路退回）",
            target: "退款单 RF-2026-0031 · 订单 SO-2026-0007",
            change: "状态 → 已批准；随后按原支付渠道发起退款。",
            amount: "¥1,280.00",
            consequence: "批准会真实出款且不可撤回。",
            confirmLabel: "确认批准并原路退款",
            reasonMin: 6,
            requireTyped: "SO-2026-0007",
            pending: false,
          });
      },
    });
    const wrapper = mount(Host, { attachTo: document.body, global: { plugins: [pinia] } });
    return wrapper;
  }

  /** Teleport 之后的弹层根节点（在 document.body 内）。 */
  function panel(): HTMLElement {
    const found = document.querySelector<HTMLElement>('[role="alertdialog"]');
    if (!found) throw new Error("没有找到确认对话框");
    return found;
  }

  function confirmButton(): HTMLButtonElement {
    const button = Array.from(panel().querySelectorAll("button")).find((item) =>
      item.textContent?.includes("确认批准并原路退款"),
    );
    if (!button) throw new Error("没有找到确认按钮");
    return button;
  }

  /** AppButton 用 aria-disabled 表达不可用状态（禁用原因写在按钮旁）。 */
  function confirmButtonDisabled(): boolean {
    const button = confirmButton();
    return button.disabled || button.getAttribute("aria-disabled") === "true";
  }

  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("显示具体对象、金额与后果，且理由不足时禁止确认", () => {
    mountDialog();

    const target = panel().querySelector<HTMLElement>('[data-testid="confirm-target"]');
    const amount = panel().querySelector<HTMLElement>('[data-testid="confirm-amount"]');
    const consequence = panel().querySelector<HTMLElement>('[data-testid="confirm-consequence"]');
    expect(target?.textContent).toContain("RF-2026-0031");
    expect(target?.textContent).toContain("SO-2026-0007");
    expect(amount?.textContent).toBe("¥1,280.00");
    expect(consequence?.textContent).toContain("不可撤回");

    const dialogText = panel().textContent ?? "";
    expect(dialogText).toContain("操作理由");
    expect(dialogText).toContain("至少 6 字");
    expect(dialogText).not.toMatch(/^\s*确定\s*$/m);

    const confirmButtons = Array.from(panel().querySelectorAll("button")).filter((item) =>
      item.textContent?.includes("确认批准并原路退款"),
    );
    expect(confirmButtons.length).toBe(1);
    expect(confirmButtonDisabled()).toBe(true);
  });

  it("填写理由但未输入确认对象时仍然禁止确认", async () => {
    mountDialog();
    const reason = panel().querySelector<HTMLTextAreaElement>("#confirm-reason");
    if (!reason) throw new Error("没有找到理由输入框");
    reason.value = "已核对订单状态与可退余额";
    reason.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();

    expect(confirmButtonDisabled()).toBe(true);
    expect(panel().textContent).toContain("SO-2026-0007");

    const typed = panel().querySelector<HTMLInputElement>('input[type="text"]');
    if (!typed) throw new Error("没有找到确认对象输入框");
    typed.value = "SO-2026-0007";
    typed.dispatchEvent(new Event("input", { bubbles: true }));
    await nextTick();
    expect(confirmButtonDisabled()).toBe(false);
  });
});

describe("MFA 两步流程状态机", () => {
  it("首次登录要求登记 MFA，并给出只显示一次的 otpauth URI 与恢复码", async () => {
    mocks.login.mockResolvedValue({
      status: "MFA_ENROLL_REQUIRED",
      challenge_token: "challenge_enroll",
      staff_id: STAFF_ID,
      enroll: {
        secret: "JBSWY3DPEHPK3PXP",
        otpauth_uri: "otpauth://totp/Starium:staff?secret=JBSWY3DPEHPK3PXP&issuer=Starium",
        recovery_codes: ["AAAA-BBBB", "CCCC-DDDD"],
      },
    });

    const auth = useAdminAuthStore(pinia);
    expect(auth.step).toBe("credential");

    await auth.login("tester", "password-1");
    expect(auth.step).toBe("enroll");
    expect(auth.enrollment?.recovery_codes).toHaveLength(2);

    // 未确认已保存恢复码前不能进入验证步骤。
    expect(mocks.mfa).not.toHaveBeenCalled();

    auth.acknowledgeEnrollment();
    expect(auth.step).toBe("verify");
    expect(auth.enrollment).toBeNull();
  });

  it("已登记 MFA 的账号跳过高风险步骤，直接进入验证", async () => {
    mocks.login.mockResolvedValue({
      status: "MFA_REQUIRED",
      challenge_token: "challenge_verify",
      staff_id: STAFF_ID,
    });

    const auth = useAdminAuthStore(pinia);
    await auth.login("tester", "password-1");
    expect(auth.step).toBe("verify");
    expect(auth.enrollment).toBeNull();
  });

  it("验证通过后保存 csrf_token 并进入已登录状态；失败则停留在验证步骤", async () => {
    mocks.login.mockResolvedValue({
      status: "MFA_REQUIRED",
      challenge_token: "challenge_verify",
      staff_id: STAFF_ID,
    });
    mocks.mfa.mockRejectedValueOnce(
      new ApiError(401, {
        code: "SMS_CODE_INVALID",
        message: "验证码不正确。",
        fields: {},
        trace_id: "trace_test",
      }),
    );

    const auth = useAdminAuthStore(pinia);
    await auth.login("tester", "password-1");
    await expect(auth.verifyMfa("000000")).rejects.toBeInstanceOf(ApiError);
    expect(auth.step).toBe("verify");
    expect(auth.isAuthenticated).toBe(false);
    expect(auth.lastError?.message).toBe("验证码不正确。");

    mocks.mfa.mockResolvedValue(mfaPayload());
    await auth.verifyMfa("123456");
    expect(auth.step).toBe("authenticated");
    expect(auth.isAuthenticated).toBe(true);
    expect(auth.can(ADMIN_PERMISSIONS.dashboard)).toBe(true);
    expect(auth.can(ADMIN_PERMISSIONS.refundApprove)).toBe(false);
    expect(auth.sessionExpiresAt).toBe("2026-01-01T00:00:00Z");
  });

  it("没有 challenge_token 时不允许直接提交验证码", async () => {
    const auth = useAdminAuthStore(pinia);
    await expect(auth.verifyMfa("123456")).rejects.toMatchObject({ code: "STATE_CONFLICT" });
    expect(mocks.mfa).not.toHaveBeenCalled();
  });

  it("退出登录后清空员工身份与权限码", async () => {
    mocks.mfa.mockResolvedValue(mfaPayload());
    const auth = useAdminAuthStore(pinia);
    await auth.login("tester", "password-1").catch(() => undefined);
    mocks.login.mockResolvedValue({
      status: "MFA_REQUIRED",
      challenge_token: "challenge_verify",
      staff_id: STAFF_ID,
    });
    await auth.login("tester", "password-1");
    await auth.verifyMfa("123456");
    expect(auth.isAuthenticated).toBe(true);

    mocks.logout.mockResolvedValue(undefined);
    await auth.logout();
    expect(auth.isAuthenticated).toBe(false);
    expect(auth.permissions).toEqual([]);
    expect(auth.step).toBe("credential");
  });
});
