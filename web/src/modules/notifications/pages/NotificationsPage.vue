<script setup lang="ts">
/**
 * 通知中心（`/account/notifications`）。
 *
 * 依据 03/FR-017：分类与未读筛选、查看、标记已读；筛选条件进 URL，刷新可恢复。
 * C27 说明：站内通知由邮件同步发送，邮件为必选通道；营销邮件默认不订阅，可退订。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { describeIdentityError, errorCodeOf, isSessionExpired } from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import {
  NOTIFICATION_CATEGORY_HINT,
  NOTIFICATION_CATEGORY_LABEL,
  fetchNotifications,
  markAllNotificationsRead,
  markNotificationRead,
  notificationCategoryLabel,
  notificationLink,
  type NotificationCategory,
  type NotificationItem,
  type NotificationStatus,
} from "@/modules/notifications/api/notifications";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime, formatRelative } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const toast = useToast();

const crumbs = [
  { label: "首页", to: "/" },
  { label: "我的账户", to: "/account" },
  { label: "通知" },
];

const items = ref<NotificationItem[]>([]);
const total = ref(0);
const page = ref(1);
const limit = 20;
const loading = ref(true);
const error = ref("");
const forbidden = ref(false);
const sessionExpired = ref(false);
const markingId = ref("");
const markingAll = ref(false);

const STATUS_OPTIONS: Array<{ value: "" | NotificationStatus; label: string }> = [
  { value: "", label: "全部" },
  { value: "UNREAD", label: "仅未读" },
  { value: "READ", label: "已读" },
];

const CATEGORY_OPTIONS: Array<{ value: "" | NotificationCategory; label: string }> = [
  { value: "", label: "全部分类" },
  { value: "transaction", label: NOTIFICATION_CATEGORY_LABEL["transaction"] ?? "交易" },
  { value: "production", label: NOTIFICATION_CATEGORY_LABEL["production"] ?? "制作" },
  { value: "loyalty", label: NOTIFICATION_CATEGORY_LABEL["loyalty"] ?? "星绒币" },
  { value: "feedback", label: NOTIFICATION_CATEGORY_LABEL["feedback"] ?? "反馈" },
  { value: "security", label: NOTIFICATION_CATEGORY_LABEL["security"] ?? "安全" },
  { value: "marketing", label: NOTIFICATION_CATEGORY_LABEL["marketing"] ?? "活动" },
];

function readQueryString(value: unknown): string {
  return typeof value === "string" ? value : "";
}

const status = ref<"" | NotificationStatus>(
  readQueryString(route.query.status) === "UNREAD"
    ? "UNREAD"
    : readQueryString(route.query.status) === "READ"
      ? "READ"
      : "",
);
const category = ref<"" | NotificationCategory>(
  (CATEGORY_OPTIONS.find((item) => item.value && item.value === readQueryString(route.query.category))
    ?.value ?? "") as "" | NotificationCategory,
);

/** 筛选条件进 URL（03 文档：可刷新恢复），值为空时移除参数。 */
function syncUrl(): void {
  const query: Record<string, string> = {};
  if (status.value) query.status = status.value;
  if (category.value) query.category = category.value;
  if (page.value > 1) query.page = String(page.value);
  void router.replace({ query });
}

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / limit)));
const unreadOnPage = computed(() => items.value.filter((item) => item.status === "UNREAD").length);
const activeCategoryHint = computed(() =>
  category.value ? NOTIFICATION_CATEGORY_HINT[category.value] ?? "" : "站内通知按分类归档；交易与安全通知不能关闭。",
);

async function load(): Promise<void> {
  loading.value = true;
  error.value = "";
  forbidden.value = false;
  sessionExpired.value = false;
  try {
    const result = await fetchNotifications({
      status: status.value || undefined,
      category: category.value || undefined,
      page: page.value,
      limit,
    });
    items.value = result.items ?? [];
    total.value = result.total ?? items.value.length;
    if (typeof result.page === "number" && result.page > 0) page.value = result.page;
  } catch (caught) {
    items.value = [];
    total.value = 0;
    error.value = describeIdentityError(caught);
    forbidden.value = errorCodeOf(caught) === "FORBIDDEN";
    sessionExpired.value = isSessionExpired(caught);
  } finally {
    loading.value = false;
  }
}

async function refresh(): Promise<void> {
  syncUrl();
  await load();
}

async function markRead(item: NotificationItem): Promise<void> {
  if (item.status !== "UNREAD") return;
  markingId.value = item.id;
  const snapshot = item.status;
  item.status = "READ";
  try {
    await markNotificationRead(item.id);
    auth.setUnread(Math.max(0, auth.unread - 1));
  } catch (caught) {
    item.status = snapshot;
    toast.error("标记已读失败", describeIdentityError(caught));
  } finally {
    markingId.value = "";
  }
}

async function markAllRead(): Promise<void> {
  markingAll.value = true;
  try {
    await markAllNotificationsRead();
    auth.setUnread(0);
    toast.success("已全部标为已读");
    await load();
  } catch (caught) {
    toast.error("操作失败", describeIdentityError(caught));
  } finally {
    markingAll.value = false;
  }
}

function goPage(next: number): void {
  const target = Math.min(Math.max(1, next), totalPages.value);
  if (target === page.value) return;
  page.value = target;
  void refresh();
}

watch([status, category], () => {
  page.value = 1;
  void refresh();
});

onMounted(load);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="crumbs" />

    <div class="mt-4 flex flex-wrap items-end justify-between gap-4">
      <SectionHeading
        :level="1"
        eyebrow="Notifications"        title="通知中心"
        description="订单、报价、制作与安全提醒都会留在这里，便于你回看处理过程。"
      />
      <div class="flex flex-wrap items-center gap-3">
        <StatusBadge :tone="auth.unread > 0 ? 'amber' : 'neutral'" :label="auth.unread > 0 ? `${auth.unread} 条未读` : '无未读'" />
        <AppButton
          size="sm"
          variant="secondary"
          :loading="markingAll"
          :disabled="items.length === 0 || unreadOnPage === 0"
          :disabled-reason="items.length === 0 ? '当前没有通知。' : '本页没有未读通知。'"
          @click="markAllRead()"
        >
          全部标为已读
        </AppButton>
      </div>
    </div>

    <SurfaceCard class="mt-6">
      <p class="text-sm leading-relaxed text-ink-muted">
        站内通知由邮件同步发送：交易、制作与安全类邮件是必选通道（发送失败不会回滚已成功的支付，
        系统会重试并留下记录）；<strong class="font-medium">营销邮件默认不订阅</strong>，
        可在
        <RouterLink class="link-underline" to="/account/profile">个人资料的「同意与订阅」</RouterLink>
        中随时订阅或退订。
      </p>
    </SurfaceCard>

    <!-- 筛选：条件进 URL -->
    <form class="mt-6 flex flex-wrap items-end gap-4" @submit.prevent="refresh()">
      <label class="space-y-1.5 text-sm">
        <span class="block font-medium text-ink-soft">读取状态</span>
        <select v-model="status" class="field-base" name="status">
          <option v-for="option in STATUS_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <label class="space-y-1.5 text-sm">
        <span class="block font-medium text-ink-soft">分类</span>
        <select v-model="category" class="field-base" name="category">
          <option v-for="option in CATEGORY_OPTIONS" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </label>
      <AppButton type="submit" size="sm" variant="secondary" :loading="loading">刷新列表</AppButton>
    </form>
    <p class="mt-2 text-xs text-ink-faint">{{ activeCategoryHint }}</p>

    <div class="mt-6">
      <SkeletonBlock v-if="loading" :lines="6" aria-label="正在读取通知列表" />

      <EmptyState
        v-else-if="sessionExpired"
        tone="error"
        title="登录状态已过期"
        description="为保护通知内容，请重新登录后再查看。"
      >
        <AppButton size="sm" to="/login?returnTo=/account/notifications">重新登录</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="forbidden"
        tone="forbidden"
        title="当前账号无权查看该列表"
        description="请确认登录的账号；如需协助，请通过公开客服邮箱联系，我们不会在此说明其他账号的通知情况。"
      />

      <EmptyState
        v-else-if="error"
        tone="error"
        title="通知加载失败"
        :description="`${error} 网络恢复后可以重试，筛选条件不会丢失。`"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="items.length === 0"
        title="没有符合条件的通知"
        :description="
          status === 'UNREAD'
            ? '当前筛选下没有未读通知；把「读取状态」切回全部可以看历史记录。'
            : '有新的订单、报价或制作进展时会出现在这里。'
        "
      >
        <AppButton v-if="status || category" size="sm" variant="secondary" @click="status = ''; category = ''">
          清除筛选
        </AppButton>
      </EmptyState>

      <template v-else>
        <ul class="space-y-4">
          <li v-for="item in items" :key="item.id">
            <SurfaceCard
              class="transition-shadow"
              :class="item.status === 'UNREAD' ? 'border-amber' : ''"
            >
              <div class="flex flex-wrap items-start justify-between gap-3">
                <div class="min-w-0">
                  <div class="flex flex-wrap items-center gap-2">
                    <StatusBadge
                      :tone="item.status === 'UNREAD' ? 'amber' : 'neutral'"
                      :label="item.status === 'UNREAD' ? '未读' : item.status === 'ARCHIVED' ? '已归档' : '已读'"
                    />
                    <StatusBadge tone="neutral" :label="notificationCategoryLabel(item.category)" />
                    <span class="text-xs text-ink-faint">{{ item.event_type }}</span>
                  </div>
                  <h2 class="mt-2 font-display text-lg">{{ item.title }}</h2>
                  <p class="mt-2 text-sm leading-relaxed text-ink-muted">{{ item.body }}</p>
                  <p class="mt-2 text-xs text-ink-faint" :title="formatDateTime(item.created_at)">
                    {{ formatRelative(item.created_at) }}
                    <template v-if="item.read_at">· 已于 {{ formatDateTime(item.read_at) }} 标记已读</template>
                  </p>
                </div>
                <div class="flex shrink-0 flex-col gap-2">
                  <AppButton
                    v-if="notificationLink(item)"
                    size="sm"
                    :to="notificationLink(item) as string"
                  >
                    查看详情
                  </AppButton>
                  <AppButton
                    size="sm"
                    variant="secondary"
                    :loading="markingId === item.id"
                    :disabled="item.status !== 'UNREAD'"
                    :disabled-reason="item.status !== 'UNREAD' ? '该通知已是已读状态。' : ''"
                    @click="markRead(item)"
                  >
                    标为已读
                  </AppButton>
                </div>
              </div>
            </SurfaceCard>
          </li>
        </ul>

        <nav v-if="totalPages > 1" class="mt-6 flex flex-wrap items-center justify-between gap-3" aria-label="通知分页">
          <p class="text-xs text-ink-faint">
            第 {{ page }} / {{ totalPages }} 页 · 共 {{ total }} 条（每页 {{ limit }} 条）
          </p>
          <div class="flex gap-3">
            <AppButton
              size="sm"
              variant="secondary"
              :disabled="page <= 1"
              :disabled-reason="page <= 1 ? '已经是第一页。' : ''"
              @click="goPage(page - 1)"
            >
              上一页
            </AppButton>
            <AppButton
              size="sm"
              variant="secondary"
              :disabled="page >= totalPages"
              :disabled-reason="page >= totalPages ? '已经是最后一页。' : ''"
              @click="goPage(page + 1)"
            >
              下一页
            </AppButton>
          </div>
        </nav>
      </template>
    </div>

    <p class="mt-8 text-xs text-ink-faint">
      通知内容不包含验证码、付款密钥或完整手机号；需要核对的金额与状态以订单页展示的服务端结果为准。
    </p>
  </div>
</template>
