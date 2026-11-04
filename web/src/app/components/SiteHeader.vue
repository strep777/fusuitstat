<script setup lang="ts">
/**
 * 站点头部：品牌、主导航、接单状态、账户入口与主题切换。
 * 键盘可用；移动端菜单可 Esc 关闭并把焦点还给触发按钮。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import { counterpartPath } from "@/app/router";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { useI18n } from "@/shared/lib/i18n";
import { BrandMark, PointsIcon } from "@/shared/ui/BrandMark";
import { AppButton } from "@/shared/ui/base";
import { usePreferencesStore } from "@/shared/stores/preferences";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const site = useSiteStore();
const preferences = usePreferencesStore();
const logoVariant = computed<"dark" | "light">(() =>
  preferences.effectiveTheme === "dark" ? "light" : "dark",
);
const { t, locale, setLocale } = useI18n();

interface NavItem {
  label: string;
  to: string;
  /** 只有启用对应功能开关才显示（未上线语言不输出假入口） */
  feature?: string;
}

/** 语言切换只在后台启用 `en_locale` 后出现：未上线的语言不输出假入口（C02/C24）。 */
const englishAvailable = computed(() => site.featureEnabled("en_locale"));
const nextLocale = computed(() => (locale.value === "en-US" ? "zh-CN" : "en-US"));

/**
 * 同一页面的另一种语言地址（由路由给出）。
 *
 * 账户/结算等尚未双语的页面没有对位地址：此时**只切界面语言、不跳转**，
 * 避免把用户带到一个"英文 URL 里的中文页"。
 */
const counterpart = computed(() => counterpartPath(route.fullPath));

async function switchLocale(): Promise<void> {
  const target = nextLocale.value;
  const path = target === "en-US" ? counterpart.value : counterpart.value;
  setLocale(target);
  if (path && path !== route.fullPath) {
    await router.push(path);
  }
}

const navItems = computed<NavItem[]>(() => {
  const items: NavItem[] = [
    { label: t("nav.works"), to: "/works" },
    { label: t("nav.commission"), to: "/commission/guide" },
    { label: t("nav.shop"), to: "/shop" },
    { label: t("nav.wash"), to: "/wash" },
    { label: t("nav.props"), to: "/props" },
    { label: t("nav.merch"), to: "/merch" },
    { label: t("nav.about"), to: "/about" },
  ];
  return items.filter((item) => !item.feature || site.featureEnabled(item.feature));
});

const scrolled = ref(false);
const menuOpen = ref(false);
const menuButton = ref<HTMLButtonElement | null>(null);

function onScroll(): void {
  scrolled.value = window.scrollY > 8;
}

function onKeydown(event: KeyboardEvent): void {
  if (event.key === "Escape" && menuOpen.value) {
    menuOpen.value = false;
    menuButton.value?.focus();
  }
}

onMounted(() => {
  window.addEventListener("scroll", onScroll, { passive: true });
  window.addEventListener("keydown", onKeydown);
  onScroll();
});

onUnmounted(() => {
  window.removeEventListener("scroll", onScroll);
  window.removeEventListener("keydown", onKeydown);
});

watch(
  () => route.fullPath,
  () => {
    menuOpen.value = false;
  },
);

const commissionLabel = computed(() => {
  if (site.commissionOpen === null) return t("nav.scheduleUnknown");
  return site.commissionOpen ? t("nav.scheduleOpen") : t("nav.scheduleClosed");
});
</script>

<template>
  <header
    class="sticky top-0 z-40 border-b bg-canvas/90 backdrop-blur-md transition-colors"
    :class="scrolled ? 'border-line' : 'border-transparent'"
  >
    <div class="container-page flex h-[76px] items-center gap-4">
      <RouterLink
        to="/"
        class="flex shrink-0 items-center gap-2.5"
        :aria-label="t('nav.brandHome')"
      >
        <BrandMark :size="36" :variant="logoVariant" decorative class="shrink-0" />
        <span class="flex flex-col leading-none">
          <span class="font-display text-[17px] font-medium tracking-tight">
            {{ t("common.brandShort") }}
          </span>
          <span
            class="mt-1 hidden text-[9px] uppercase tracking-[0.16em] text-ink-muted sm:block"
          >
            {{ t("common.brandEnglish") }}
          </span>
        </span>
      </RouterLink>

      <nav class="ml-5 hidden items-center gap-6 lg:flex" :aria-label="t('nav.mainLabel')">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="text-[13px] text-ink-muted transition-colors hover:text-ink"
          active-class="!font-medium !text-ink"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="ml-auto flex items-center gap-1.5">
        <RouterLink
          to="/commission/schedules"
          class="hidden items-center gap-2 px-2 text-[11px] text-ink-muted transition-colors hover:text-ink sm:inline-flex"
        >
          <span
            class="size-1.5 rounded-full"
            :class="site.commissionOpen ? 'bg-jade' : 'bg-ink-faint'"
            aria-hidden="true"
          />
          {{ commissionLabel }}
        </RouterLink>

        <!-- 语言切换：仅在后台启用 en_locale 后出现（未上线语言不输出假入口） -->
        <button
          v-if="englishAvailable"
          type="button"
          lang="en-US"
          class="hidden size-9 items-center justify-center rounded-full text-xs text-ink-muted transition-colors hover:bg-canvas-sunk hover:text-ink sm:inline-flex"
          :aria-label="`${t('common.language')}：${nextLocale === 'en-US' ? t('common.languageEn') : t('common.languageZh')}`"
          @click="switchLocale()"
        >
          <span aria-hidden="true">{{ nextLocale === "en-US" ? "EN" : "中" }}</span>
        </button>

        <button
          type="button"
          class="inline-flex size-9 items-center justify-center rounded-full text-ink-muted transition-colors hover:bg-canvas-sunk hover:text-ink"
          :aria-label="preferences.theme === 'dark' ? t('nav.themeToLight') : t('nav.themeToDark')"
          :aria-pressed="preferences.theme === 'dark'"
          @click="preferences.toggleTheme()"
        >
          <span aria-hidden="true">{{ preferences.theme === "dark" ? "☀" : "☾" }}</span>
        </button>

        <RouterLink
          v-if="auth.isAuthenticated"
          to="/account"
          class="hidden items-center gap-2 rounded-full px-2 py-1.5 text-sm text-ink-soft transition-colors hover:bg-canvas-sunk sm:inline-flex"
        >
          <PointsIcon :size="18" :label="`${auth.displayName}${t('nav.unread', { count: auth.unread })}`" />
          <span class="max-w-[8rem] truncate">{{ auth.displayName }}</span>
          <span
            v-if="auth.unread > 0"
            class="rounded-full bg-brand px-1.5 text-[10px] font-medium text-white"
            aria-hidden="true"
          >
            {{ auth.unread > 99 ? "99+" : auth.unread }}
          </span>
        </RouterLink>
        <AppButton v-else to="/login" variant="primary" size="sm" class="ml-1 hidden sm:inline-flex">
          {{ t("nav.login") }}
        </AppButton>

        <button
          ref="menuButton"
          type="button"
          class="inline-flex size-10 items-center justify-center rounded-full text-ink transition-colors hover:bg-canvas-sunk lg:hidden"
          :aria-expanded="menuOpen"
          aria-controls="site-mobile-menu"
          :aria-label="menuOpen ? t('nav.closeMenu') : t('nav.openMenu')"
          @click="menuOpen = !menuOpen"
        >
          <span aria-hidden="true">{{ menuOpen ? "✕" : "☰" }}</span>
        </button>
      </div>
    </div>

    <div
      v-if="menuOpen"
      id="site-mobile-menu"
      class="border-t border-line bg-canvas lg:hidden"
    >
      <nav class="container-page grid gap-1 py-4" :aria-label="t('nav.mobileLabel')">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="rounded-lg px-3 py-3 text-[15px] text-ink-soft transition-colors hover:bg-canvas-sunk hover:text-ink"
        >
          {{ item.label }}
        </RouterLink>
        <div class="my-1 border-t border-line" />
        <RouterLink
          v-if="auth.isAuthenticated"
          to="/account"
          class="rounded-lg px-3 py-3 text-[15px] text-ink-soft hover:bg-canvas-sunk"
        >
          {{ t("nav.account") }}
        </RouterLink>
        <RouterLink
          v-else
          to="/login"
          class="rounded-lg px-3 py-3 text-[15px] text-ink-soft hover:bg-canvas-sunk"
        >
          {{ t("nav.loginMobile") }}
        </RouterLink>
        <RouterLink
          to="/contact"
          class="rounded-lg px-3 py-3 text-[15px] text-ink-soft hover:bg-canvas-sunk"
        >
          {{ t("nav.contact") }}
        </RouterLink>
        <button
          v-if="englishAvailable"
          type="button"
          class="rounded-lg px-3 py-3 text-left text-[15px] text-ink-soft hover:bg-canvas-sunk"
          @click="switchLocale()"
        >
          {{ nextLocale === "en-US" ? t("common.languageEn") : t("common.languageZh") }}
        </button>
      </nav>
    </div>
  </header>
</template>
