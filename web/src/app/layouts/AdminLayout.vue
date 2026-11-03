<script setup lang="ts">
/**
 * 后台外壳：深色侧栏、分组导航、当前员工与角色、权限过滤、会话提示。
 *
 * 约束（03/11 文档）：
 * - 菜单只显示当前账号有权限的入口；缺少权限的入口集中列出并写明权限码。
 * - 界面隐藏不能代替授权，服务端仍会逐动作校验。
 * - 不显示密钥、手机号明文、地址明文或签名 URL。
 */
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import { RouterLink, RouterView, useRoute, useRouter } from "vue-router";

import {
  ADMIN_NAV_SECTIONS,
  filterAdminNav,
  useAdminAuthStore,
} from "@/modules/admin/stores/adminAuth";
import { InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { BrandMark, StarLoader } from "@/shared/ui/BrandMark";
import { StatusBadge } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const route = useRoute();
const router = useRouter();
const toast = useToast();

const navOpen = ref(false);
const loggingOut = ref(false);
const now = ref(Date.now());
let timer: ReturnType<typeof setInterval> | null = null;

onMounted(() => {
  timer = setInterval(() => {
    now.value = Date.now();
  }, 30_000);
});
onBeforeUnmount(() => {
  if (timer) clearInterval(timer);
});

const nav = computed(() => filterAdminNav(auth.permissions, auth.isAuthenticated));

const grouped = computed(() =>
  ADMIN_NAV_SECTIONS.map((section) => ({
    section,
    items: nav.value.granted.filter((item) => item.section === section),
  })).filter((group) => group.items.length > 0),
);

const currentTitle = computed(() => (route.meta.title as string | undefined) ?? "运营后台");

/** 会话有效期提示：剩余不足 5 分钟时提醒员工先保存。 */
const sessionHint = computed(() => {
  if (!auth.sessionExpiresAt) return "";
  const expires = new Date(auth.sessionExpiresAt).getTime();
  if (Number.isNaN(expires)) return "";
  const remainingMinutes = Math.round((expires - now.value) / 60_000);
  if (remainingMinutes <= 0) return "后台会话已到期，请重新登录。";
  if (remainingMinutes <= 5) return `后台会话将在 ${remainingMinutes} 分钟内到期，请先保存正在编辑的内容。`;
  return "";
});

async function logout(): Promise<void> {
  loggingOut.value = true;
  try {
    await auth.logout();
    toast.success("已退出运营后台", "本机不再保留后台会话，写操作将需要重新登录。");
    await router.replace({ name: "admin-login" });
  } finally {
    loggingOut.value = false;
  }
}
</script>

<template>
  <div class="min-h-screen bg-canvas">
    <a class="skip-link" href="#admin-main">跳到主要内容</a>

    <!-- 移动端遮罩 -->
    <div
      v-if="navOpen"
      class="fixed inset-0 z-40 bg-[#17121C]/60 lg:hidden"
      aria-hidden="true"
      @click="navOpen = false"
    />

    <!-- 深色侧栏：桌面固定，移动端抽屉 -->
    <aside
      id="admin-nav"
      class="fixed inset-y-0 left-0 z-50 flex w-[272px] flex-col border-r-2 border-ink bg-[#17121C] text-[#FFFCF2] transition-transform lg:translate-x-0"
      :class="navOpen ? 'translate-x-0' : '-translate-x-full'"
    >
      <div class="flex h-[72px] shrink-0 items-center gap-3 border-b-2 border-white/15 px-5">
        <BrandMark :size="34" variant="light" decorative />
        <div class="min-w-0">
          <p class="truncate font-display text-sm font-bold">弈星纪元</p>
          <p class="font-mono text-[9px] uppercase tracking-[0.18em] text-white/45">Studio Console</p>
        </div>
        <button
          type="button"
          class="ml-auto inline-flex size-9 items-center justify-center border-2 border-white/30 text-white/70 lg:hidden"
          aria-label="关闭导航"
          @click="navOpen = false"
        >
          ✕
        </button>
      </div>

      <nav class="flex-1 overflow-y-auto px-3 py-5" aria-label="后台主导航">
        <div class="space-y-6">
          <div v-for="group in grouped" :key="group.section">
            <p class="px-2 font-mono text-[10px] uppercase tracking-[0.18em] text-white/40">
              {{ group.section }}
            </p>
            <ul class="mt-2 space-y-1">
              <li v-for="(item, index) in group.items" :key="item.id">
                <RouterLink
                  :to="item.path"
                  class="group flex items-center gap-2.5 border-2 border-transparent px-2.5 py-2 text-sm text-white/65 transition-colors hover:border-white/30 hover:text-white"
                  active-class="!border-[#FFFCF2] !bg-lemon !text-[#17121C] font-bold"
                  :title="item.description"
                  @click="navOpen = false"
                >
                  <span class="font-mono text-[9px] opacity-50">{{ String(index + 1).padStart(2, "0") }}</span>
                  <span class="truncate">{{ item.label }}</span>
                </RouterLink>
              </li>
            </ul>
          </div>

          <details v-if="nav.denied.length" class="border-2 border-dashed border-white/20 px-3 py-2">
            <summary class="cursor-pointer text-xs font-medium text-white/55">
              有 {{ nav.denied.length }} 个模块无权限
            </summary>
            <ul class="mt-2 space-y-1.5">
              <li v-for="item in nav.denied" :key="item.id" class="text-[11px] text-white/40">
                <span class="text-white/60">{{ item.label }}</span>
                <span class="ml-1 font-mono">[{{ item.permission }}]</span>
              </li>
            </ul>
            <p class="mt-2 text-[11px] leading-relaxed text-white/35">
              隐藏入口不代替授权；服务端仍逐动作校验并记录审计。
            </p>
          </details>
          <p v-else-if="auth.isAuthenticated" class="px-2 text-[11px] leading-relaxed text-white/35">
            当前账号已具备全部模块权限。服务端仍逐动作校验。
          </p>
        </div>
      </nav>

      <div class="shrink-0 border-t-2 border-white/15 p-4">
        <div class="flex items-center justify-between gap-2">
          <div class="min-w-0">
            <p class="truncate text-sm font-bold">{{ auth.displayName }}</p>
            <p class="truncate font-mono text-[10px] text-white/45">{{ auth.roleLabel }}</p>
          </div>
          <StatusBadge
            :tone="auth.mfaEnabled ? 'jade' : 'wine'"
            :label="auth.mfaEnabled ? 'MFA ON' : 'MFA OFF'"
          />
        </div>
        <div class="mt-3 grid gap-2">
          <RouterLink
            to="/"
            class="flex min-h-[40px] items-center justify-between border-2 border-white/25 px-3 text-xs text-white/70 hover:border-[#FFFCF2] hover:text-white"
          >
            返回前台 <span aria-hidden="true">↗</span>
          </RouterLink>
          <button
            v-if="auth.isAuthenticated"
            type="button"
            class="flex min-h-[40px] items-center justify-center border-2 border-white/25 px-3 text-xs font-bold text-white/70 hover:border-[#FFFCF2] hover:text-white disabled:opacity-50"
            :disabled="loggingOut"
            @click="logout"
          >
            {{ loggingOut ? "正在退出…" : "退出登录" }}
          </button>
        </div>
      </div>
    </aside>

    <div class="flex min-h-screen flex-col lg:pl-[272px]">
      <header class="sticky top-0 z-30 border-b-2 border-ink bg-canvas/95 backdrop-blur">
        <div class="flex min-h-[64px] items-center gap-3 px-4 sm:px-6">
          <button
            type="button"
            class="inline-flex size-10 items-center justify-center border-2 border-ink bg-surface lg:hidden"
            :aria-expanded="navOpen ? 'true' : 'false'"
            aria-controls="admin-nav"
            aria-label="展开导航"
            @click="navOpen = true"
          >
            ☰
          </button>
          <div class="min-w-0">
            <p class="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-muted">Console / 运营后台</p>
            <p class="mt-0.5 truncate font-display text-lg font-bold leading-tight">{{ currentTitle }}</p>
          </div>

          <div class="ml-auto flex items-center gap-2 sm:gap-3">
            <span class="hidden border-2 border-ink bg-surface px-2.5 py-1 font-mono text-[10px] text-ink-soft sm:inline-flex">
              {{ auth.roleLabel }} / {{ auth.displayName }}
            </span>
            <StatusBadge
              :tone="auth.mfaEnabled ? 'jade' : 'wine'"
              :label="auth.mfaEnabled ? 'MFA 已启用' : 'MFA 未启用'"
            />
          </div>
        </div>

        <p
          v-if="sessionHint"
          class="border-t-2 border-ink bg-lemon px-4 py-2 font-mono text-[11px] font-bold text-[#17121C] sm:px-6"
        >
          {{ sessionHint }}
        </p>
      </header>

      <main id="admin-main" class="w-full max-w-[1500px] flex-1 space-y-6 px-4 py-6 sm:px-6 lg:px-8">
        <InlineNotice
          v-if="auth.sessionExpired"
          tone="danger"
          title="后台会话已过期"
          description="为保护运营数据，会话到期后必须重新登录并完成 MFA；未提交的编辑内容需要重新填写。"
        />

        <RouterView v-slot="{ Component }">
          <Suspense>
            <component :is="Component" :key="route.fullPath" />
            <template #fallback>
              <StarLoader message="正在打开后台页面" :size="72" />
            </template>
          </Suspense>
        </RouterView>

        <footer class="border-t-2 border-ink/15 pt-4 font-mono text-[10px] uppercase tracking-wide text-ink-faint">
          当前页面 / {{ currentTitle }} · 后台 noindex，不进入搜索索引与预渲染
        </footer>
      </main>
    </div>

    <!-- 轻量提示容器 -->
    <div class="pointer-events-none fixed bottom-4 right-4 z-[60] flex w-full max-w-sm flex-col gap-2" aria-live="polite">
      <div
        v-for="item in toast.items.value"
        :key="item.id"
        class="pointer-events-auto rounded-sm border-2 border-ink bg-surface px-4 py-3 text-sm shadow-[4px_4px_0_var(--color-ink)]"
        :class="item.tone === 'error' ? 'border-wine' : item.tone === 'success' ? 'border-jade' : 'border-ink'"
      >
        <p class="font-bold">{{ item.title }}</p>
        <p v-if="item.description" class="mt-1 text-xs text-ink-muted">{{ item.description }}</p>
        <button
          v-if="item.actionLabel"
          type="button"
          class="mt-2 text-xs underline decoration-line-strong underline-offset-2"
          @click="item.onAction?.(), toast.dismiss(item.id)"
        >
          {{ item.actionLabel }}
        </button>
        <button
          type="button"
          class="mt-2 ml-3 text-xs text-ink-faint hover:text-ink"
          @click="toast.dismiss(item.id)"
        >
          关闭提示
        </button>
      </div>
    </div>

    <p class="sr-only" aria-live="polite">
      当前员工 {{ auth.displayName }}，会话到期时间 {{ formatDateTime(auth.sessionExpiresAt) }}
    </p>
  </div>
</template>
