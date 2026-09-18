<script setup lang="ts">
/**
 * 站点头部：品牌、主导航、接单状态、账户入口与主题切换。
 * 键盘可用；移动端菜单可 Esc 关闭并把焦点还给触发按钮。
 */
import { computed, onMounted, onUnmounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { BrandMark, PointsIcon } from "@/shared/ui/BrandMark";
import { AppButton } from "@/shared/ui/base";
import { usePreferencesStore } from "@/shared/stores/preferences";

interface NavItem {
  label: string;
  to: string;
  /** 只有启用对应功能开关才显示（未上线语言不输出假入口） */
  feature?: string;
}

const route = useRoute();
const auth = useAuthStore();
const site = useSiteStore();
const preferences = usePreferencesStore();

const navItems = computed<NavItem[]>(() => {
  const items: NavItem[] = [
    { label: "作品", to: "/works" },
    { label: "系列", to: "/fursuits" },
    { label: "委托", to: "/commission/guide" },
    { label: "角色", to: "/characters" },
    { label: "商城", to: "/shop" },
    { label: "公告", to: "/news" },
    { label: "关于", to: "/about" },
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
  if (site.commissionOpen === null) return "查看档期";
  return site.commissionOpen ? "委托开放中" : "委托暂未开放";
});
</script>

<template>
  <header
    class="sticky top-0 z-40 border-b transition-colors duration-200"
    :class="
      scrolled
        ? 'border-line bg-canvas/95 backdrop-blur supports-[backdrop-filter]:bg-canvas/80'
        : 'border-transparent bg-canvas'
    "
  >
    <div class="container-page flex h-16 items-center gap-4 sm:h-20">
      <RouterLink to="/" class="flex items-center gap-3" aria-label="弈星纪元兽装工作室首页">
        <BrandMark :size="40" variant="dark" decorative class="shrink-0" />
        <span class="flex flex-col leading-tight">
          <span class="font-display text-base font-semibold tracking-tight sm:text-lg">弈星纪元</span>
          <span class="hidden text-[11px] text-ink-faint sm:block">STARIUM FURSUIT STUDIO</span>
        </span>
      </RouterLink>

      <nav class="ml-4 hidden items-center gap-1 lg:flex" aria-label="主导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="rounded-sm px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-surface-muted hover:text-ink"
          active-class="text-ink font-medium"
        >
          {{ item.label }}
        </RouterLink>
      </nav>

      <div class="ml-auto flex items-center gap-2">
        <RouterLink
          to="/commission/schedules"
          class="hidden items-center gap-2 rounded-full border border-line px-3 py-1.5 text-xs text-ink-muted transition-colors hover:border-amber hover:text-amber-strong sm:inline-flex"
        >
          <span
            class="size-1.5 rounded-full"
            :class="site.commissionOpen ? 'bg-jade' : 'bg-ink-faint'"
            aria-hidden="true"
          />
          {{ commissionLabel }}
        </RouterLink>

        <button
          type="button"
          class="inline-flex size-11 items-center justify-center rounded-sm border border-line text-ink-muted transition-colors hover:border-amber hover:text-amber-strong"
          :aria-label="preferences.theme === 'dark' ? '切换到浅色主题' : '切换到深色主题'"
          :aria-pressed="preferences.theme === 'dark'"
          @click="preferences.toggleTheme()"
        >
          <span aria-hidden="true">{{ preferences.theme === "dark" ? "☀" : "☾" }}</span>
        </button>

        <RouterLink
          v-if="auth.isAuthenticated"
          to="/account"
          class="hidden items-center gap-2 rounded-sm border border-line px-3 py-2 text-sm text-ink-soft transition-colors hover:border-amber sm:inline-flex"
        >
          <PointsIcon :size="18" :label="`${auth.displayName}，${auth.unread} 条未读通知`" />
          <span class="max-w-[8rem] truncate">{{ auth.displayName }}</span>
          <span
            v-if="auth.unread > 0"
            class="rounded-full bg-amber px-1.5 text-[11px] font-semibold text-white"
            aria-hidden="true"
          >
            {{ auth.unread > 99 ? "99+" : auth.unread }}
          </span>
        </RouterLink>
        <AppButton v-else to="/login" variant="secondary" size="sm" class="hidden sm:inline-flex">
          登录
        </AppButton>

        <button
          ref="menuButton"
          type="button"
          class="inline-flex size-11 items-center justify-center rounded-sm border border-line text-ink-soft lg:hidden"
          :aria-expanded="menuOpen"
          aria-controls="site-mobile-menu"
          aria-label="打开导航菜单"
          @click="menuOpen = !menuOpen"
        >
          <span aria-hidden="true">{{ menuOpen ? "✕" : "☰" }}</span>
        </button>
      </div>
    </div>

    <div
      v-if="menuOpen"
      id="site-mobile-menu"
      class="border-t border-line bg-surface lg:hidden"
    >
      <nav class="container-page grid gap-1 py-4" aria-label="移动导航">
        <RouterLink
          v-for="item in navItems"
          :key="item.to"
          :to="item.to"
          class="rounded-sm px-3 py-3 text-base text-ink-soft hover:bg-surface-muted"
        >
          {{ item.label }}
        </RouterLink>
        <div class="hairline my-2" />
        <RouterLink
          v-if="auth.isAuthenticated"
          to="/account"
          class="rounded-sm px-3 py-3 text-base text-ink-soft hover:bg-surface-muted"
        >
          我的账户
        </RouterLink>
        <RouterLink v-else to="/login" class="rounded-sm px-3 py-3 text-base text-ink-soft hover:bg-surface-muted">
          手机号登录
        </RouterLink>
        <RouterLink to="/contact" class="rounded-sm px-3 py-3 text-base text-ink-soft hover:bg-surface-muted">
          联系与反馈
        </RouterLink>
      </nav>
    </div>
  </header>
</template>
