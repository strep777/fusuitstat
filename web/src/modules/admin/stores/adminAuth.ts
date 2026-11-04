/**
 * 后台员工会话状态（Pinia）。
 *
 * 关键约束（03/06/10 文档）：
 * - 后台是**独立会话**：即使客户已登录，也不能继承员工权限；登录请求不带前台会话依赖。
 * - 权限码来自登录（或 `/admin/auth/me`）响应；界面里的 `can(code)` 只用于显示，
 *   服务端仍会逐动作校验权限，UI 隐藏不能代替授权。
 * - 两步登录：口令 → MFA（TOTP 或恢复码）；首次登录必须先登记 MFA（展示 otpauth URI 与恢复码，只显示一次）。
 * - CSRF token 必须在同一 SPA 会话内保存：登录后立刻交给 `setCsrfToken`，后续写操作自动带 CSRF 头。
 */

import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { adminApi, type AdminMeResponse, type EnrollPayload, type MfaResponse } from "@/modules/admin/api/admin";
import { ApiError, clearCsrfToken, setCsrfToken } from "@/shared/lib/http";

export type AdminLoginStep = "credential" | "enroll" | "verify" | "authenticated";

/** 权限码唯一维护位置：路由 `meta.permission`、侧边导航、`can()` 都读这里。 */
export const ADMIN_PERMISSIONS = {
  dashboard: "dashboard.read",
  contentWrite: "content.write",
  contentPublish: "content.publish",
  media: "media.manage",
  catalogWrite: "catalog.write",
  quoteRead: "quote.read",
  quoteReview: "quote.review",
  schedule: "schedule.manage",
  production: "production.write",
  orderRead: "order.read",
  orderFulfill: "order.fulfill",
  paymentRead: "payment.read",
  refundRequest: "refund.request",
  refundApprove: "refund.approve",
  financeReconcile: "finance.reconcile",
  feedbackReview: "feedback.review",
  pointsAdjust: "points.adjust",
  userRead: "user.read",
  userManage: "user.manage",
  staffManage: "staff.manage",
  settingsManage: "settings.manage",
  auditRead: "audit.read",
  opsRead: "ops.read",
} as const;

export type AdminPermission = (typeof ADMIN_PERMISSIONS)[keyof typeof ADMIN_PERMISSIONS];

export interface AdminNavItem {
  id: string;
  label: string;
  path: string;
  permission: AdminPermission;
  section: string;
  description: string;
}

/** 侧边导航目录。菜单项只做入口过滤；真正的授权在服务端。 */
export const ADMIN_NAV: readonly AdminNavItem[] = [
  { id: "dashboard", label: "运营概览", path: "/admin/dashboard", permission: ADMIN_PERMISSIONS.dashboard, section: "概览", description: "待付款订单、待审退款、对账差异、任务失败" },
  { id: "content", label: "内容管理", path: "/admin/content", permission: ADMIN_PERMISSIONS.contentWrite, section: "内容与商品", description: "作品、公告、系列、FAQ、法律文本的草稿与发布" },
  { id: "media", label: "素材库", path: "/admin/media", permission: ADMIN_PERMISSIONS.media, section: "内容与商品", description: "授权信息、扫描状态、替代文本、引用关系" },
  { id: "products", label: "商品与库存", path: "/admin/products", permission: ADMIN_PERMISSIONS.catalogWrite, section: "内容与商品", description: "商品、SKU、价格、库存调整与低库存" },
  { id: "characters", label: "角色领养", path: "/admin/characters", permission: ADMIN_PERMISSIONS.catalogWrite, section: "内容与商品", description: "独占角色内容、授权规则与售出展示" },
  { id: "applications", label: "服务与掉落申请", path: "/admin/applications", permission: ADMIN_PERMISSIONS.catalogWrite, section: "内容与商品", description: "洗毛预约与掉落申请的审批、购买窗口与拒绝理由" },
  { id: "quotes", label: "询价与报价", path: "/admin/quotes", permission: ADMIN_PERMISSIONS.quoteReview, section: "交易与履约", description: "分派、补件、发布报价版本、拒绝理由" },
  { id: "estimates", label: "估价请求", path: "/admin/estimates", permission: ADMIN_PERMISSIONS.quoteRead, section: "交易与履约", description: "公开估价提交、联系方式与跟进状态" },
  { id: "schedules", label: "档期与申请", path: "/admin/schedules", permission: ADMIN_PERMISSIONS.schedule, section: "交易与履约", description: "容量、时间窗口、普通申请与候补、审批" },
  { id: "commissions", label: "制作单", path: "/admin/commissions", permission: ADMIN_PERMISSIONS.production, section: "交易与履约", description: "节点更新、私密相册、确认记录、变更单" },
  { id: "orders", label: "订单", path: "/admin/orders", permission: ADMIN_PERMISSIONS.orderRead, section: "交易与履约", description: "明细、发货、关闭原因、售后入口" },
  { id: "payments", label: "收款", path: "/admin/payments", permission: ADMIN_PERMISSIONS.paymentRead, section: "资金", description: "收款记录、异常单、渠道查单" },
  { id: "refunds", label: "退款", path: "/admin/refunds", permission: ADMIN_PERMISSIONS.refundRequest, section: "资金", description: "申请与批准分离，原路退款与失败重查" },
  { id: "reconciliation", label: "对账", path: "/admin/reconciliation", permission: ADMIN_PERMISSIONS.financeReconcile, section: "资金", description: "日账单、差异归因、处置与导出审计" },
  { id: "feedback", label: "反馈与奖励", path: "/admin/feedback", permission: ADMIN_PERMISSIONS.feedbackReview, section: "客户与系统", description: "去重、回复、奖励等级、申诉" },
  { id: "points", label: "星绒币", path: "/admin/points", permission: ADMIN_PERMISSIONS.pointsAdjust, section: "客户与系统", description: "流水查询、调整申请与审批；不允许直接改余额" },
  { id: "users", label: "用户", path: "/admin/users", permission: ADMIN_PERMISSIONS.userRead, section: "客户与系统", description: "脱敏详情、会话撤销、限制状态与原因" },
  { id: "staff", label: "员工与权限", path: "/admin/staff", permission: ADMIN_PERMISSIONS.staffManage, section: "客户与系统", description: "员工、角色、MFA 状态、权限变更审计" },
  { id: "settings", label: "经营设置", path: "/admin/settings", permission: ADMIN_PERMISSIONS.settingsManage, section: "客户与系统", description: "经营信息、规则版本、通知模板、功能开关" },
  { id: "audit", label: "审计", path: "/admin/audit", permission: ADMIN_PERMISSIONS.auditRead, section: "客户与系统", description: "审计查询与前后快照摘要" },
  { id: "jobs", label: "任务与死信", path: "/admin/jobs", permission: ADMIN_PERMISSIONS.opsRead, section: "客户与系统", description: "任务失败、人工重试与死信处理" },
];

export const ADMIN_NAV_SECTIONS: readonly string[] = ["概览", "内容与商品", "交易与履约", "资金", "客户与系统"];

export function adminNavFor(permission: AdminPermission): AdminNavItem | undefined {
  return ADMIN_NAV.find((item) => item.permission === permission);
}

export interface NavFilterResult {
  granted: AdminNavItem[];
  denied: AdminNavItem[];
}

/**
 * 菜单权限过滤（纯函数，便于测试）。
 * 未登录时一切都不可见；`denied` 用于在界面上说明缺少哪些权限码，而不是静默消失。
 */
export function filterAdminNav(permissions: readonly string[], authenticated: boolean): NavFilterResult {
  if (!authenticated) return { granted: [], denied: [...ADMIN_NAV] };
  const granted: AdminNavItem[] = [];
  const denied: AdminNavItem[] = [];
  for (const item of ADMIN_NAV) {
    if (permissions.includes(item.permission)) granted.push(item);
    else denied.push(item);
  }
  return { granted, denied };
}

export interface StaffIdentity {
  id: string;
  display_name: string;
  login_name: string;
}

export const useAdminAuthStore = defineStore("adminAuth", () => {
  const staff = ref<StaffIdentity | null>(null);
  const roles = ref<string[]>([]);
  const permissions = ref<string[]>([]);
  const sessionExpiresAt = ref<string | null>(null);
  const mfaEnabled = ref(false);

  const step = ref<AdminLoginStep>("credential");
  const challengeToken = ref<string | null>(null);
  const pendingStaffId = ref<string | null>(null);
  const pendingLoginName = ref<string | null>(null);
  const enrollment = ref<EnrollPayload | null>(null);

  const loading = ref(false);
  const loaded = ref(false);
  /** 会话过期后置位：外壳据此显示「请重新登录」，而不是静默失败。 */
  const sessionExpired = ref(false);
  const lastError = ref<{ code: string; message: string } | null>(null);

  const isAuthenticated = computed(() => staff.value !== null);
  const permissionSet = computed(() => new Set(permissions.value));
  const displayName = computed(() => staff.value?.display_name ?? "未登录员工");
  const roleLabel = computed(() => (roles.value.length ? roles.value.join("、") : "未分配角色"));

  /** 界面级权限判断。服务端仍会逐动作校验，不能作为授权依据。 */
  function can(code: string): boolean {
    return permissionSet.value.has(code);
  }
  function canAll(codes: readonly string[]): boolean {
    return codes.every((code) => permissionSet.value.has(code));
  }
  function canAny(codes: readonly string[]): boolean {
    return codes.some((code) => permissionSet.value.has(code));
  }

  function resetChallenge(): void {
    step.value = "credential";
    challengeToken.value = null;
    pendingStaffId.value = null;
    enrollment.value = null;
  }

  function clearSession(): void {
    staff.value = null;
    roles.value = [];
    permissions.value = [];
    sessionExpiresAt.value = null;
    mfaEnabled.value = false;
    loaded.value = true;
    clearCsrfToken();
    resetChallenge();
  }

  /**
   * 登录响应是扁平结构，`/admin/auth/me` 是 `{staff, roles}` 嵌套结构。
   * 这里统一归一化，避免刷新后台后显示 `[object Object]` 或丢失员工姓名。
   */
  function applySession(payload: MfaResponse | AdminMeResponse): void {
    const nested = "staff" in payload ? payload.staff : null;
    const staffId = nested?.id ?? ("staff_id" in payload ? payload.staff_id : "");
    const displayName = nested?.display_name ?? ("display_name" in payload ? payload.display_name : "");
    const loginName = nested?.login_name ?? ("login_name" in payload ? payload.login_name : "");
    const roleNames = (payload.roles ?? []).map((role) =>
      typeof role === "string" ? role : role.name || role.code,
    );
    const mfaEnabledValue = nested?.mfa_enabled ?? ("mfa_enabled" in payload ? payload.mfa_enabled : false);

    staff.value = { id: staffId, display_name: displayName || "未命名员工", login_name: loginName };
    permissions.value = [...payload.permissions];
    roles.value = roleNames;
    sessionExpiresAt.value = payload.session_expires_at;
    mfaEnabled.value = Boolean(mfaEnabledValue);
    sessionExpired.value = false;
    lastError.value = null;
    loaded.value = true;
    resetChallenge();
    step.value = "authenticated";
  }

  /** 第一步：口令。返回下一步状态（MFA_REQUIRED 或 MFA_ENROLL_REQUIRED）。 */
  async function login(loginName: string, password: string): Promise<AdminLoginStep> {
    loading.value = true;
    lastError.value = null;
    try {
      const payload = await adminApi.login({ login_name: loginName, password });
      challengeToken.value = payload.challenge_token;
      pendingStaffId.value = payload.staff_id;
      pendingLoginName.value = loginName;
      enrollment.value = payload.enroll ?? null;
      step.value = payload.status === "MFA_ENROLL_REQUIRED" ? "enroll" : "verify";
      return step.value;
    } catch (error) {
      resetChallenge();
      lastError.value = toErrorView(error);
      throw error;
    } finally {
      loading.value = false;
    }
  }

  /**
   * 第二步：TOTP 或恢复码。
   * 成功后才拿到 `csrf_token`，必须立即保存到内存，写操作会自动带 CSRF 头。
   */
  async function verifyMfa(code: string): Promise<void> {
    if (!challengeToken.value || !pendingStaffId.value) {
      throw new ApiError(409, {
        code: "STATE_CONFLICT",
        message: "登录流程已失效，请重新输入口令。",
        fields: {},
        trace_id: "",
      });
    }
    loading.value = true;
    lastError.value = null;
    try {
      const payload = await adminApi.mfa({
        challenge_token: challengeToken.value,
        code,
        staff_id: pendingStaffId.value,
      });
      setCsrfToken(payload.csrf_token);
      applySession(payload);
    } catch (error) {
      lastError.value = toErrorView(error);
      throw error;
    } finally {
      loading.value = false;
    }
  }

  /** 首次登录登记：确认已保存恢复码后进入验证步骤。 */
  function acknowledgeEnrollment(): void {
    if (step.value !== "enroll") return;
    enrollment.value = null;
    step.value = "verify";
  }

  /** 刷新或直达深层链接时恢复会话；失败视为未登录，不抛给调用方。 */
  async function loadSession(): Promise<boolean> {
    loading.value = true;
    try {
      const payload = await adminApi.me();
      applySession(payload);
      return true;
    } catch (error) {
      const sessionError = error as Partial<ApiError>;
      if (sessionError?.needsLogin || sessionError?.status === 401) {
        clearSession();
        return false;
      }
      // 网络错误不能让已登录员工被踢出：保留现状，由页面显示错误态。
      loaded.value = true;
      lastError.value = toErrorView(error);
      return isAuthenticated.value;
    } finally {
      loading.value = false;
    }
  }

  async function logout(): Promise<void> {
    try {
      await adminApi.logout();
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      // 退出失败也要清空本地会话，避免界面显示已退出却仍能操作。
    } finally {
      clearSession();
    }
  }

  /** 401 统一入口：清理会话并标记过期，由路由守卫引导重新登录。 */
  function handleSessionExpired(): void {
    clearSession();
    sessionExpired.value = true;
  }

  return {
    staff,
    roles,
    permissions,
    sessionExpiresAt,
    mfaEnabled,
    step,
    challengeToken,
    pendingStaffId,
    pendingLoginName,
    enrollment,
    loading,
    loaded,
    sessionExpired,
    lastError,
    isAuthenticated,
    displayName,
    roleLabel,
    can,
    canAll,
    canAny,
    login,
    verifyMfa,
    acknowledgeEnrollment,
    resetChallenge,
    loadSession,
    logout,
    handleSessionExpired,
  };
});

function toErrorView(error: unknown): { code: string; message: string } {
  const typed = error as Partial<ApiError>;
  return {
    code: typed?.code ?? "INTERNAL_ERROR",
    message: typed?.message || "操作失败，请稍后重试。",
  };
}
