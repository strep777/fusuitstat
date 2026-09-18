/**
 * 路由与页面标题、SEO meta 的集中声明。
 *
 * 约束（03/11 文档）：过滤条件进 URL；私密页面 noindex 且 no-store；
 * 未启用的可选功能不注册路由（未上线语言不输出假入口）。
 */

import type { RouteRecordRaw } from "vue-router";
import { createRouter, createWebHistory } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";

const SiteLayout = () => import("@/app/layouts/SiteLayout.vue");
const HomePage = () => import("@/modules/content/pages/HomePage.vue");
const ContentPage = () => import("@/modules/content/pages/ContentPage.vue");
const ContentDetailPage = () => import("@/modules/content/pages/ContentDetailPage.vue");
const AboutPage = () => import("@/modules/content/pages/AboutPage.vue");
const ContactPage = () => import("@/modules/content/pages/ContactPage.vue");
const FaqPage = () => import("@/modules/content/pages/FaqPage.vue");
const CarePage = () => import("@/modules/content/pages/CarePage.vue");
const LegalPage = () => import("@/modules/content/pages/LegalPage.vue");

const WorksPage = () => import("@/modules/content/pages/WorksPage.vue");
const WorkDetailPage = () => import("@/modules/content/pages/WorkDetailPage.vue");
const SeriesListPage = () => import("@/modules/catalog/pages/SeriesListPage.vue");
const SeriesDetailPage = () => import("@/modules/catalog/pages/SeriesDetailPage.vue");
const CharactersPage = () => import("@/modules/catalog/pages/CharactersPage.vue");
const CharacterDetailPage = () => import("@/modules/catalog/pages/CharacterDetailPage.vue");
const ShopPage = () => import("@/modules/catalog/pages/ShopPage.vue");
const ProductDetailPage = () => import("@/modules/catalog/pages/ProductDetailPage.vue");

const CommissionGuidePage = () => import("@/modules/commissions/pages/CommissionGuidePage.vue");
const CommissionSchedulesPage = () => import("@/modules/commissions/pages/CommissionSchedulesPage.vue");
const CommissionContractPage = () => import("@/modules/commissions/pages/CommissionContractPage.vue");
const InquiryNewPage = () => import("@/modules/commissions/pages/InquiryNewPage.vue");

const LoginPage = () => import("@/modules/identity/pages/LoginPage.vue");
const AccountOverviewPage = () => import("@/modules/identity/pages/AccountOverviewPage.vue");
const AccountProfilePage = () => import("@/modules/identity/pages/AccountProfilePage.vue");
const AccountSecurityPage = () => import("@/modules/identity/pages/AccountSecurityPage.vue");
const AccountAddressesPage = () => import("@/modules/identity/pages/AccountAddressesPage.vue");
const AccountQuotesPage = () => import("@/modules/commissions/pages/AccountQuotesPage.vue");
const AccountQuoteDetailPage = () => import("@/modules/commissions/pages/AccountQuoteDetailPage.vue");
const AccountCommissionPage = () => import("@/modules/commissions/pages/AccountCommissionPage.vue");
const AccountApplicationsPage = () => import("@/modules/catalog/pages/AccountApplicationsPage.vue");
const NotificationsPage = () => import("@/modules/notifications/pages/NotificationsPage.vue");

const CartPage = () => import("@/modules/commerce/pages/CartPage.vue");
const CheckoutPage = () => import("@/modules/commerce/pages/CheckoutPage.vue");
const AccountOrdersPage = () => import("@/modules/commerce/pages/AccountOrdersPage.vue");
const AccountOrderDetailPage = () => import("@/modules/commerce/pages/AccountOrderDetailPage.vue");
const PayPage = () => import("@/modules/payments/pages/PayPage.vue");
const PaymentReturnPage = () => import("@/modules/payments/pages/PaymentReturnPage.vue");
const AfterSalesListPage = () => import("@/modules/commerce/pages/AfterSalesListPage.vue");
const AfterSaleDetailPage = () => import("@/modules/commerce/pages/AfterSaleDetailPage.vue");
const PointsPage = () => import("@/modules/loyalty/pages/PointsPage.vue");
const FeedbackNewPage = () => import("@/modules/feedback/pages/FeedbackNewPage.vue");
const FeedbackListPage = () => import("@/modules/feedback/pages/FeedbackListPage.vue");
const FeedbackDetailPage = () => import("@/modules/feedback/pages/FeedbackDetailPage.vue");

const NotFoundPage = () => import("@/modules/content/pages/NotFoundPage.vue");
const MaintenancePage = () => import("@/modules/content/pages/MaintenancePage.vue");

/** 需要登录的路由。401 引导登录并保留不含敏感信息的目标路径。 */
const AUTH_REQUIRED = { requiresAuth: true } as const;

const routes: RouteRecordRaw[] = [
  {
    path: "/",
    component: SiteLayout,
    children: [
      { path: "", name: "home", component: HomePage, meta: { title: "首页" } },

      // ---- 品牌与内容 ----
      { path: "about", name: "about", component: AboutPage, meta: { title: "关于工作室" } },
      { path: "contact", name: "contact", component: ContactPage, meta: { title: "联系与反馈" } },
      { path: "news", name: "news", component: ContentPage, meta: { title: "公告", kind: "news" } },
      {
        path: "news/:slug",
        name: "news-detail",
        component: ContentDetailPage,
        meta: { title: "公告", kind: "news" },
      },
      { path: "works", name: "works", component: WorksPage, meta: { title: "作品集" } },
      { path: "works/:slug", name: "work-detail", component: WorkDetailPage, meta: { title: "作品" } },
      { path: "fursuits", name: "series", component: SeriesListPage, meta: { title: "系列与规格" } },
      {
        path: "fursuits/:slug",
        name: "series-detail",
        component: SeriesDetailPage,
        meta: { title: "系列详情" },
      },
      { path: "faq", name: "faq", component: FaqPage, meta: { title: "常见问题" } },
      { path: "care", name: "care", component: CarePage, meta: { title: "养护与清洁" } },
      { path: "legal/:slug", name: "legal", component: LegalPage, meta: { title: "规则与条款" } },
      { path: "pages/:slug", name: "custom-page", component: ContentDetailPage, meta: { title: "页面", kind: "page" } },

      // ---- 委托 ----
      {
        path: "commission/guide",
        name: "commission-guide",
        component: CommissionGuidePage,
        meta: { title: "委托指南" },
      },
      {
        path: "commission/schedules",
        name: "commission-schedules",
        component: CommissionSchedulesPage,
        meta: { title: "档期与名额" },
      },
      {
        path: "commission/contract",
        name: "commission-contract",
        component: CommissionContractPage,
        meta: { title: "合同说明与示例" },
      },
      {
        path: "commission/new",
        name: "inquiry-new",
        component: InquiryNewPage,
        meta: { title: "提交询价", ...AUTH_REQUIRED },
      },

      // ---- 目录 ----
      { path: "characters", name: "characters", component: CharactersPage, meta: { title: "角色领养" } },
      {
        path: "characters/:slug",
        name: "character-detail",
        component: CharacterDetailPage,
        meta: { title: "角色详情" },
      },
      { path: "shop", name: "shop", component: ShopPage, meta: { title: "商城" } },
      { path: "shop/:slug", name: "product-detail", component: ProductDetailPage, meta: { title: "商品详情" } },

      // ---- 登录 ----
      { path: "login", name: "login", component: LoginPage, meta: { title: "登录", layout: "plain" } },

      // ---- 账户中心 ----
      { path: "account", name: "account", component: AccountOverviewPage, meta: { title: "我的账户", ...AUTH_REQUIRED } },
      {
        path: "account/profile",
        name: "account-profile",
        component: AccountProfilePage,
        meta: { title: "个人资料", ...AUTH_REQUIRED },
      },
      {
        path: "account/security",
        name: "account-security",
        component: AccountSecurityPage,
        meta: { title: "账号与安全", ...AUTH_REQUIRED },
      },
      {
        path: "account/addresses",
        name: "account-addresses",
        component: AccountAddressesPage,
        meta: { title: "收货地址", ...AUTH_REQUIRED },
      },
      {
        path: "account/quotes",
        name: "account-quotes",
        component: AccountQuotesPage,
        meta: { title: "我的询价与报价", ...AUTH_REQUIRED },
      },
      {
        path: "account/quotes/:id",
        name: "account-quote-detail",
        component: AccountQuoteDetailPage,
        meta: { title: "询价详情", ...AUTH_REQUIRED },
      },
      {
        path: "account/commissions/:id",
        name: "account-commission",
        component: AccountCommissionPage,
        meta: { title: "制作进度", ...AUTH_REQUIRED },
      },
      {
        path: "account/applications",
        name: "account-applications",
        component: AccountApplicationsPage,
        meta: { title: "掉落与服务申请", ...AUTH_REQUIRED },
      },
      {
        path: "account/notifications",
        name: "account-notifications",
        component: NotificationsPage,
        meta: { title: "通知", ...AUTH_REQUIRED },
      },
      { path: "cart", name: "cart", component: CartPage, meta: { title: "购物车" } },
      {
        path: "checkout",
        name: "checkout",
        component: CheckoutPage,
        meta: { title: "结算", ...AUTH_REQUIRED },
      },
      {
        path: "account/orders",
        name: "account-orders",
        component: AccountOrdersPage,
        meta: { title: "我的订单", ...AUTH_REQUIRED },
      },
      {
        path: "account/orders/:id",
        name: "account-order-detail",
        component: AccountOrderDetailPage,
        meta: { title: "订单详情", ...AUTH_REQUIRED },
      },
      { path: "pay/:id", name: "pay", component: PayPage, meta: { title: "支付", ...AUTH_REQUIRED } },
      {
        path: "payment/return",
        name: "payment-return",
        component: PaymentReturnPage,
        meta: { title: "支付结果核对" },
      },
      {
        path: "account/after-sales",
        name: "account-after-sales",
        component: AfterSalesListPage,
        meta: { title: "售后与退款", ...AUTH_REQUIRED },
      },
      {
        path: "account/after-sales/:id",
        name: "account-after-sale-detail",
        component: AfterSaleDetailPage,
        meta: { title: "售后详情", ...AUTH_REQUIRED },
      },
      {
        path: "account/points",
        name: "account-points",
        component: PointsPage,
        meta: { title: "星绒币", ...AUTH_REQUIRED },
      },
      {
        path: "feedback/new",
        name: "feedback-new",
        component: FeedbackNewPage,
        meta: { title: "提交反馈", ...AUTH_REQUIRED },
      },
      {
        path: "account/feedback",
        name: "account-feedback",
        component: FeedbackListPage,
        meta: { title: "我的反馈", ...AUTH_REQUIRED },
      },
      {
        path: "account/feedback/:id",
        name: "account-feedback-detail",
        component: FeedbackDetailPage,
        meta: { title: "反馈详情", ...AUTH_REQUIRED },
      },

      { path: "maintenance", name: "maintenance", component: MaintenancePage, meta: { title: "维护中", layout: "plain" } },
      { path: ":pathMatch(.*)*", name: "not-found", component: NotFoundPage, meta: { title: "页面不存在" } },
    ],
  },
];

export const router = createRouter({
  history: createWebHistory(),
  routes,
  scrollBehavior(to, _from, saved) {
    if (saved) return saved;
    if (to.hash) return { el: to.hash, behavior: "smooth" };
    return { top: 0 };
  },
});

const SITE_NAME = "弈星纪元兽装工作室";

/** 私密路由：noindex + no-store（03 文档）。 */
const PRIVATE_PREFIXES = ["/account", "/admin", "/pay", "/checkout", "/cart", "/login", "/payment", "/feedback"];

function isPrivate(path: string): boolean {
  return PRIVATE_PREFIXES.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
}

router.beforeEach(async (to) => {
  const auth = useAuthStore();

  if (to.meta.requiresAuth) {
    if (!auth.loaded) {
      await auth.loadMe().catch(() => null);
    }
    if (!auth.isAuthenticated) {
      // 返回目标只允许本站相对路径（10 文档）。
      return { name: "login", query: { returnTo: to.fullPath } };
    }
  }
  return true;
});

router.afterEach((to) => {
  const title = (to.meta.title as string | undefined) ?? "";
  document.title = title ? `${title} · ${SITE_NAME}` : SITE_NAME;

  let robots = document.querySelector<HTMLMetaElement>('meta[name="robots"]');
  if (!robots) {
    robots = document.createElement("meta");
    robots.name = "robots";
    document.head.appendChild(robots);
  }
  if (isPrivate(to.path)) {
    robots.content = "noindex, nofollow";
  } else if (to.name === "not-found") {
    robots.content = "noindex, follow";
  } else {
    robots.content = "index, follow";
  }
});

export function registerOptionalRoutes(site: ReturnType<typeof useSiteStore>): void {
  // C15/C18/C20/C29 的可选路由只在开关启用后注册，避免输出假入口。
  const optional: Array<{
    flag: string;
    path: string;
    name: string;
    component: RouteRecordRaw["component"];
  }> = [
    {
      flag: "points_redemption",
      path: "/rewards",
      name: "rewards",
      component: () => import("@/modules/loyalty/pages/RewardsPage.vue"),
    },
    {
      flag: "client_stories",
      path: "/stories",
      name: "stories",
      component: () => import("@/modules/content/pages/ContentPage.vue"),
    },
    {
      flag: "rush_rounds",
      path: "/commission/rush/:roundId",
      name: "commission-rush",
      component: () => import("@/modules/commissions/pages/RushRoundPage.vue"),
    },
  ];
  for (const item of optional) {
    if (!site.featureEnabled(item.flag)) continue;
    if (router.hasRoute(item.name)) continue;
    // vue-router 4.6 的 RouteRecordRaw 是联合类型，直接传对象字面量会命中
    // 需要 redirect/children 的分支；这里显式断言为单视图记录。
    router.addRoute({
      path: item.path,
      name: item.name,
      component: item.component,
      meta: { title: item.name, requiresAuth: true },
    } as RouteRecordRaw);
  }
}
