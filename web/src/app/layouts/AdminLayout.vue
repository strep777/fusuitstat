<script setup lang="ts">
/**
 * 后台外壳：侧边导航、当前员工与角色、权限过滤后的菜单、退出登录。
 *
 * 约束（03/11 文档）：
 * - 菜单只显示当前账号有权限的入口；同时把缺少权限的入口列出来并写明权限码，
 *   并明确「界面隐藏不能代替授权，服务端仍会逐动作校验」。
 * - 键盘可用：跳转到主内容、导航可 Tab、退出登录有明确状态。
 * - 不显示任何密钥、手机号明文、地址明文或签名 URL；这里只显示员工登录名与角色。
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
import { AppButton, StatusBadge } from "@/shared/ui/base";
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

    <header class="sticky top-0 z-30 border-b border-line bg-surface">
      <div class="flex flex-wrap items-center gap-4 px-4 py-3 lg:px-6">
        <BrandMark :size="32" decorative />
        <div class="min-w-0">
          <p class="eyebrow">运营后台</p>
          <p class="truncate font-display text-base">弈星纪元兽装工作室</p>
        </div>

        <div class="ml-auto flex flex-wrap items-center gap-3 text-sm">
          <div class="hidden text-right sm:block">
            <p class="font-medium">{{ auth.displayName }}</p>
            <p class="text-xs text-ink-faint">角色：{{ auth.roleLabel }}</p>
          </div>
          <StatusBadge
            :tone="auth.mfaEnabled ? 'jade' : 'wine'"
            :label="auth.mfaEnabled ? 'MFA 已启用' : 'MFA 未启用'"
          />
          <AppButton
            v-if="auth.isAuthenticated"
            size="sm"
            variant="secondary"
            :loading="loggingOut"
            @click="logout"
          >
            退出登录
          </AppButton>
        </div>

        <button
          type="button"
          class="w-full rounded-sm border border-line-strong px-3 py-2 text-sm text-ink-soft lg:hidden"
          :aria-expanded="navOpen ? 'true' : 'false'"
          aria-controls="admin-nav"
          @click="navOpen = !navOpen"
        >
          {{ navOpen ? "收起导航" : "展开导航" }}
        </button>
      </div>

      <p v-if="sessionHint" class="border-t border-amber bg-amber-soft px-4 py-2 text-xs text-amber-strong">
        {{ sessionHint }}
      </p>
    </header>

    <div class="mx-auto flex w-full max-w-[1600px] gap-6 px-4 py-6 lg:px-6">
      <nav
        id="admin-nav"
        aria-label="后台主导航"
        :class="navOpen ? 'block' : 'hidden lg:block'"
        class="w-full shrink-0 lg:w-72"
      >
        <div class="space-y-5">
          <div v-for="group in grouped" :key="group.section">
            <p class="eyebrow px-2">{{ group.section }}</p>
            <ul class="mt-2 space-y-1">
              <li v-for="item in group.items" :key="item.id">
                <RouterLink
                  :to="item.path"
                  class="block rounded-sm px-3 py-2 text-sm text-ink-soft transition-colors hover:bg-surface-muted hover:text-amber-strong"
                  active-class="!bg-amber-soft !text-amber-strong font-medium"
                  :title="item.description"
                  @click="navOpen = false"
                >
                  {{ item.label }}
                </RouterLink>
              </li>
            </ul>
          </div>

          <!-- 权限不足的入口：不静默消失，说明缺哪个权限码 -->
          <details v-if="nav.denied.length" class="rounded-lg border border-dashed border-line-strong bg-surface-muted px-3 py-2">
            <summary class="cursor-pointer text-xs font-medium text-ink-muted">
              有 {{ nav.denied.length }} 个模块当前账号无权限
            </summary>
            <ul class="mt-2 space-y-1.5">
              <li v-for="item in nav.denied" :key="item.id" class="text-xs text-ink-faint">
                <span class="text-ink-soft">{{ item.label }}</span>
                <span class="ml-1 font-mono">[{{ item.permission }}]</span>
                <span>缺少权限，入口已隐藏</span>
              </li>
            </ul>
            <p class="mt-2 text-xs text-ink-faint">
              服务端仍会逐动作校验权限码：即使直接调用接口，无权限的请求也会被拒绝并记录审计。
            </p>
          </details>

          <p v-else-if="auth.isAuthenticated" class="px-2 text-xs text-ink-faint">
            当前账号已具备全部模块权限。服务端仍会逐动作校验权限码并记录审计。
          </p>
        </div>
      </nav>

      <main id="admin-main" class="min-w-0 flex-1 space-y-6">
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

        <footer class="border-t border-line pt-4 text-xs text-ink-faint">
          <p>
            当前页面：{{ currentTitle }} · 后台页面 noindex，不进入搜索索引、不预渲染个人资料。
          </p>
        </footer>
      </main>
    </div>

    <!-- 轻量提示容器 -->
    <div class="pointer-events-none fixed bottom-4 right-4 z-40 flex w-full max-w-sm flex-col gap-2" aria-live="polite">
      <div
        v-for="item in toast.items.value"
        :key="item.id"
        class="pointer-events-auto rounded-lg border bg-surface px-4 py-3 text-sm shadow-lift"
        :class="item.tone === 'error' ? 'border-wine' : item.tone === 'success' ? 'border-jade' : 'border-line'"
      >
        <p class="font-medium">{{ item.title }}</p>
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
