import { createPinia } from "pinia";
import { createApp, defineComponent, h } from "vue";
import { RouterView } from "vue-router";

import { adminRouter, safeReturnTo } from "@/app/admin-router";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { ApiError } from "@/shared/lib/http";
import { usePreferencesStore } from "@/shared/stores/preferences";
import "@/assets/main.css";

/**
 * 运营后台入口（独立构建入口 `admin.html`，挂载点 `#admin`）。
 *
 * 与前台入口的区别：
 * - 使用独立路由表 `admin-router`，未登录一律回到 `/admin/login`；
 * - 独立员工会话（独立 cookie），不继承前台登录状态；
 * - 后台页面一律 noindex（`admin.html` 已声明，路由切换后由 `admin-router` 维持）。
 */
const AdminRoot = defineComponent({
  name: "AdminRoot",
  setup() {
    return () => h(RouterView);
  },
});

const app = createApp(AdminRoot);
const pinia = createPinia();

app.use(pinia);
app.use(adminRouter);

// 后台同样尊重主题与减弱动效偏好，但不写入任何隐私数据。
const preferences = usePreferencesStore(pinia);
preferences.apply();

/** 收到 401 时统一清理会话并回到登录页，避免界面残留已登录假象。 */
function redirectToLogin(): void {
  const auth = useAdminAuthStore(pinia);
  auth.handleSessionExpired();
  if (adminRouter.currentRoute.value.name === "admin-login") return;
  const returnTo = safeReturnTo(adminRouter.currentRoute.value.fullPath);
  void adminRouter.replace({ name: "admin-login", query: { returnTo } });
}

// 页面加载函数都应自行捕获错误；这里兜底处理漏出的 401 与未捕获异常。
app.config.errorHandler = (error) => {
  if (error instanceof ApiError && error.needsLogin) redirectToLogin();
};

if (typeof window !== "undefined") {
  window.addEventListener("unhandledrejection", (event) => {
    const reason = event.reason;
    if (reason instanceof ApiError && reason.needsLogin) {
      event.preventDefault();
      redirectToLogin();
    }
  });
}

void adminRouter.isReady().then(() => {
  app.mount("#admin");
});
