/**
 * 运营后台独立路由（`admin.html` → `src/app/admin-main.ts`）。
 *
 * 与前台路由完全分离：后台使用独立员工会话（独立 cookie），不继承前台登录状态。
 * 守卫只做两件事：
 * 1. 未登录 → 跳到 `/admin/login`，并把不含敏感信息的目标路径放进 `returnTo`；
 * 2. 权限码不足 → 放行到路由本身，但页面用 `PermissionGate` / 说明区块告诉员工缺哪个权限码。
 *    界面隐藏不能代替授权：服务端仍会逐动作校验权限码。
 */

import type { Router, RouterHistory, RouteRecordRaw, _RouteRecordProps } from "vue-router";
import { createRouter, createWebHistory } from "vue-router";

import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";

const AdminLayout = () => import("@/app/layouts/AdminLayout.vue");
const AdminLoginPage = () => import("@/modules/admin/pages/AdminLoginPage.vue");
const DashboardPage = () => import("@/modules/admin/pages/DashboardPage.vue");
const ContentListPage = () => import("@/modules/admin/pages/ContentListPage.vue");
const ContentEditPage = () => import("@/modules/admin/pages/ContentEditPage.vue");
const MediaPage = () => import("@/modules/admin/pages/MediaPage.vue");
const ProductsPage = () => import("@/modules/admin/pages/ProductsPage.vue");
const ProductEditPage = () => import("@/modules/admin/pages/ProductEditPage.vue");
const CharactersPage = () => import("@/modules/admin/pages/CharactersPage.vue");
const QuotesPage = () => import("@/modules/admin/pages/QuotesPage.vue");
const SchedulesPage = () => import("@/modules/admin/pages/SchedulesPage.vue");
const CommissionsPage = () => import("@/modules/admin/pages/CommissionsPage.vue");
const OrdersPage = () => import("@/modules/admin/pages/OrdersPage.vue");
const OrderDetailPage = () => import("@/modules/admin/pages/OrderDetailPage.vue");
const PaymentsPage = () => import("@/modules/admin/pages/PaymentsPage.vue");
const RefundsPage = () => import("@/modules/admin/pages/RefundsPage.vue");
const ReconciliationPage = () => import("@/modules/admin/pages/ReconciliationPage.vue");
const FeedbackPage = () => import("@/modules/admin/pages/FeedbackPage.vue");
const PointsPage = () => import("@/modules/admin/pages/PointsPage.vue");
const UsersPage = () => import("@/modules/admin/pages/UsersPage.vue");
const StaffPage = () => import("@/modules/admin/pages/StaffPage.vue");
const SettingsPage = () => import("@/modules/admin/pages/SettingsPage.vue");
const AuditPage = () => import("@/modules/admin/pages/AuditPage.vue");
const JobsPage = () => import("@/modules/admin/pages/JobsPage.vue");

/** 每个后台路由都必须声明权限码；`admin/login` 以外不允许出现没有权限码的页面。 */
function page(
  path: string,
  name: string,
  title: string,
  permission: string,
  component: NonNullable<RouteRecordRaw["component"]>,
  props: _RouteRecordProps = false,
): RouteRecordRaw {
  const record = {
    path,
    name,
    component,
    props,
    meta: { title, permission },
  } as RouteRecordRaw;
  return record;
}

const routes: RouteRecordRaw[] = [
  // 顶层路由的 path 必须带前导斜杠（vue-router 会在创建时直接抛错）。
  page("/admin/login", "admin-login", "员工登录", "", AdminLoginPage),
  {
    // 后台整体挂在 `/admin` 下（03 文档：后台独立入口）。
    // 父级写明 `/admin` 后，子路由的 `"dashboard"` 等相对路径会解析为
    // `/admin/dashboard`，与 Nginx 的 `/admin.html` + SPA 回退配置一致。
    path: "/admin",
    component: AdminLayout,
    children: [
      { path: "", redirect: { name: "admin-dashboard" } },
      page("dashboard", "admin-dashboard", "运营概览", ADMIN_PERMISSIONS.dashboard, DashboardPage),
      page("content", "admin-content", "内容管理", ADMIN_PERMISSIONS.contentWrite, ContentListPage),
      page("content/new", "admin-content-new", "新建内容", ADMIN_PERMISSIONS.contentWrite, ContentEditPage),
      page("content/:id", "admin-content-edit", "编辑内容", ADMIN_PERMISSIONS.contentWrite, ContentEditPage),
      page("media", "admin-media", "素材库", ADMIN_PERMISSIONS.media, MediaPage),
      page("products", "admin-products", "商品与库存", ADMIN_PERMISSIONS.catalogWrite, ProductsPage),
      page("products/new", "admin-product-new", "新建商品", ADMIN_PERMISSIONS.catalogWrite, ProductEditPage),
      page("products/:id", "admin-product-edit", "编辑商品", ADMIN_PERMISSIONS.catalogWrite, ProductEditPage),
      page("characters", "admin-characters", "角色领养", ADMIN_PERMISSIONS.catalogWrite, CharactersPage),
      page("quotes", "admin-quotes", "询价与报价", ADMIN_PERMISSIONS.quoteReview, QuotesPage),
      page("schedules", "admin-schedules", "档期与申请", ADMIN_PERMISSIONS.schedule, SchedulesPage),
      page("commissions", "admin-commissions", "制作单", ADMIN_PERMISSIONS.production, CommissionsPage),
      page("orders", "admin-orders", "订单", ADMIN_PERMISSIONS.orderRead, OrdersPage),
      page("orders/:id", "admin-order-detail", "订单详情", ADMIN_PERMISSIONS.orderRead, OrderDetailPage),
      page("payments", "admin-payments", "收款与异常单", ADMIN_PERMISSIONS.paymentRead, PaymentsPage),
      page("refunds", "admin-refunds", "退款", ADMIN_PERMISSIONS.refundRequest, RefundsPage),
      page("reconciliation", "admin-reconciliation", "对账", ADMIN_PERMISSIONS.financeReconcile, ReconciliationPage),
      page("feedback", "admin-feedback", "反馈与奖励", ADMIN_PERMISSIONS.feedbackReview, FeedbackPage),
      page("points", "admin-points", "星绒币与调整", ADMIN_PERMISSIONS.pointsAdjust, PointsPage),
      page("users", "admin-users", "用户", ADMIN_PERMISSIONS.userRead, UsersPage),
      page("staff", "admin-staff", "员工与权限", ADMIN_PERMISSIONS.staffManage, StaffPage),
      page("settings", "admin-settings", "经营设置", ADMIN_PERMISSIONS.settingsManage, SettingsPage),
      page("audit", "admin-audit", "审计", ADMIN_PERMISSIONS.auditRead, AuditPage),
      page("jobs", "admin-jobs", "任务与死信", ADMIN_PERMISSIONS.opsRead, JobsPage),
      {
        path: ":pathMatch(.*)*",
        name: "admin-not-found",
        component: () => import("@/modules/admin/pages/AdminNotFoundPage.vue"),
        meta: { title: "页面不存在", permission: "" },
      },
    ],
  },
];

/**
 * 创建后台路由实例。测试可传入内存历史（`createMemoryHistory`），
 * 避免依赖浏览器地址栏；生产入口使用默认的 Web 历史。
 */
export function createAdminRouter(history?: RouterHistory): Router {
  return createRouter({
    history: history ?? createWebHistory(),
    routes,
    scrollBehavior(_to, _from, saved) {
      return saved ?? { top: 0 };
    },
  });
}

export const adminRouter = createAdminRouter();
registerAdminGuards(adminRouter);

/** 只接受本站相对路径，避免开放重定向（10 文档）。 */
export function safeReturnTo(value: unknown): string {
  if (typeof value !== "string") return "/admin/dashboard";
  if (!value.startsWith("/") || value.startsWith("//")) return "/admin/dashboard";
  if (!value.startsWith("/admin")) return "/admin/dashboard";
  if (value.startsWith("/admin/login")) return "/admin/dashboard";
  return value;
}

const ADMIN_TITLE = "运营后台 · 弈星纪元兽装工作室";

/**
 * 守卫：未登录 → `/admin/login`；权限码不足不在这里拦，交给页面显示说明。
 * 测试可用 `createAdminRouter` + `registerAdminGuards` 搭配内存历史。
 */
export function registerAdminGuards(target: Router): void {
  target.beforeEach(async (to) => {
    const auth = useAdminAuthStore();

    // 登录页不依赖会话；已登录员工再访问登录页时直接回到概览。
    if (to.name === "admin-login") {
      if (!auth.loaded) await auth.loadSession().catch(() => false);
      if (auth.isAuthenticated) return { name: "admin-dashboard" };
      return true;
    }

    if (!auth.loaded) {
      await auth.loadSession().catch(() => false);
    }
    if (!auth.isAuthenticated) {
      return { name: "admin-login", query: { returnTo: to.fullPath } };
    }
    return true;
  });

  // 后台页面一律 noindex（`admin.html` 已声明，这里保证客户端路由切换后仍然保持）。
  target.afterEach((to) => {
    const title = (to.meta.title as string | undefined) ?? "";
    document.title = title ? `${title} · ${ADMIN_TITLE}` : ADMIN_TITLE;

    let robots = document.querySelector<HTMLMetaElement>('meta[name="robots"]');
    if (!robots) {
      robots = document.createElement("meta");
      robots.name = "robots";
      document.head.appendChild(robots);
    }
    robots.content = "noindex, nofollow, noarchive";
  });
}

export default adminRouter;
