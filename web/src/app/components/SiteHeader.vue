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
  <header class="sticky top-0 z-40 border-b-2 border-ink bg-canvas">
    <!-- 工作室状态条：只用事实信息，不做虚假倒计时。 -->
    <div class="bg-ink text-canvas">
      <div
        class="container-page flex h-8 items-center justify-between gap-4 font-mono text-[10px] uppercase tracking-[0.16em]"
      >
        <p class="truncate">上海 · 手工兽装工作室</p>
        <div class="hidden shrink-0 items-center gap-5 md:flex">
          <span>全装 / 半装 / 单头</span>
          <span class="text-lemon">手工制作 · 全国寄送</span>
        </div>
      </div>
    </div>

    <div class="container-page flex h-16 items-center gap-3 sm:h-[72px]">
      <RouterLink
        to="/"
        class="flex shrink-0 items-center gap-2.5"
        :aria-label="t('nav.brandHome')"
      >
        <BrandMark :size="38" :variant="logoVariant" decorative class="shrink-0" />
        <span class="flex flex-col leading-none">
          <span class="font-display text-lg font-bold tracking-tight">
            {{ t("common.brandShort") }}
          </span>
          <span
            class="mt-1 hidden font-mono text-[9px] uppercase tracking-[0.16em] text-ink-muted sm:block"
          >
            {{ t("common.brandEnglish") }}
          </span>
        </span>
      </RouterLink>

      <nav class="ml-4 hidden items-center gap-0.5 lg:flex" :aria-label="t('nav.mainLabel')">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="rounded-full border-2 border-transparent px-3 py-2 text-[13px] font-semibold text-ink-soft transition-colors hover:border-ink hover:text-ink"
          active-class="border-ink !bg-lemon !text-ink shadow-[2px_2px_0_var(--color-ink)]"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="ml-auto flex items-center gap-2">
        <RouterLink
          to="/commission/schedules"
          class="hidden items-center gap-1.5 rounded-full border-2 border-ink bg-jade-soft px-3 py-1.5 font-mono text-[10px] font-bold uppercase tracking-wide text-jade transition-colors hover:bg-jade hover:text-white sm:inline-flex"
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
          class="hidden size-10 items-center justify-center rounded-full border-2 border-ink bg-surface font-mono text-xs font-bold text-ink transition-colors hover:bg-lemon sm:inline-flex"
          :aria-label="`${t('common.language')}：${nextLocale === 'en-US' ? t('common.languageEn') : t('common.languageZh')}`"
          @click="switchLocale()"
        >
          <span aria-hidden="true">{{ nextLocale === "en-US" ? "EN" : "中" }}</span>
        </button>

        <button
          type="button"
          class="inline-flex size-10 items-center justify-center rounded-full border-2 border-ink bg-surface text-ink transition-colors hover:bg-lemon"
          :aria-label="preferences.theme === 'dark' ? t('nav.themeToLight') : t('nav.themeToDark')"
          :aria-pressed="preferences.theme === 'dark'"
          @click="preferences.toggleTheme()"
        >
          <span aria-hidden="true">{{ preferences.theme === "dark" ? "☀" : "☾" }}</span>
        </button>

        <RouterLink
          v-if="auth.isAuthenticated"
          to="/account"
          class="hidden items-center gap-2 rounded-full border-2 border-ink bg-surface px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-lemon-soft sm:inline-flex"
        >
          <PointsIcon :size="18" :label="`${auth.displayName}${t('nav.unread', { count: auth.unread })}`" />
          <span class="max-w-[8rem] truncate">{{ auth.displayName }}</span>
          <span
            v-if="auth.unread > 0"
            class="rounded-full bg-brand px-1.5 font-mono text-[10px] font-bold text-white"
            aria-hidden="true"
          >
            {{ auth.unread > 99 ? "99+" : auth.unread }}
          </span>
        </RouterLink>
        <AppButton v-else to="/login" variant="primary" size="sm" class="hidden sm:inline-flex">
          {{ t("nav.login") }}
        </AppButton>

        <button
          ref="menuButton"
          type="button"
          class="inline-flex size-10 items-center justify-center rounded-full border-2 border-ink bg-surface text-ink lg:hidden"
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
      class="border-t-2 border-ink bg-canvas-sunk lg:hidden"
    >
      <nav class="container-page grid grid-cols-2 gap-2 py-4" :aria-label="t('nav.mobileLabel')">
        <RouterLink
          v-for="(item, index) in navItems"
          :key="item.to"
          :to="item.to"
          class="flex items-center gap-3 rounded-lg border-2 border-ink bg-surface px-4 py-3 text-base font-semibold text-ink shadow-[3px_3px_0_var(--color-ink)]"
        >
          <span class="font-mono text-[10px] text-ink-faint">{{ String(index + 1).padStart(2, "0") }}</span>
          {{ item.label }}
        </RouterLink>
        <div class="col-span-2 my-1 border-t-2 border-ink/15" />
        <RouterLink
          v-if="auth.isAuthenticated"
          to="/account"
          class="rounded-lg px-4 py-3 text-base text-ink-soft hover:bg-lemon-soft"
        >
          {{ t("nav.account") }}
        </RouterLink>
        <RouterLink
          v-else
          to="/login"
          class="rounded-lg px-4 py-3 text-base text-ink-soft hover:bg-lemon-soft"
        >
          {{ t("nav.loginMobile") }}
        </RouterLink>
        <RouterLink
          to="/contact"
          class="rounded-lg px-4 py-3 text-base text-ink-soft hover:bg-lemon-soft"
        >
          {{ t("nav.contact") }}
        </RouterLink>
        <button
          v-if="englishAvailable"
          type="button"
          class="rounded-lg px-4 py-3 text-left text-base text-ink-soft hover:bg-lemon-soft"
          @click="switchLocale()"
        >
          {{ nextLocale === "en-US" ? t("common.languageEn") : t("common.languageZh") }}
        </button>
      </nav>
    </div>
  </header>
</template>
