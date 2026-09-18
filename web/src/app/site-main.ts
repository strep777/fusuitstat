/** 前台入口：Vue 应用、Pinia、路由与全局样式。 */

import { createPinia } from "pinia";
import { createApp } from "vue";

import App from "@/app/App.vue";
import { router } from "@/app/router";
import { useSiteStore } from "@/modules/content/stores/site";
import { usePreferencesStore } from "@/shared/stores/preferences";
import "@/assets/main.css";

const app = createApp(App);
const pinia = createPinia();

app.use(pinia);
app.use(router);

const preferences = usePreferencesStore(pinia);
preferences.apply();

router.isReady().then(async () => {
  app.mount("#app");
  // 可选路由依赖功能开关；开关来自服务端公开配置，加载完成后再注册。
  const site = useSiteStore(pinia);
  await site.load().catch(() => null);
  const { registerOptionalRoutes } = await import("@/app/router");
  registerOptionalRoutes(site);
});
