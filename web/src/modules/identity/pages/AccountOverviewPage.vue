<script setup lang="ts">
/**
 * 账户总览（`/account`）。
 *
 * 信息顺序（03 文档）：待付款 → 待决定的报价 → 待确认阶段 → 售后 → 星绒币 → 最新通知。
 * 每块独立 loading / empty / error，并给出对应深链；不把「没有数据」和「加载失败」混为一谈。
 */
import { computed, onMounted, ref } from "vue";

import {
  PENDING_ACTION_META,
  describeIdentityError,
  errorCodeOf,
  type PendingActionKey,
} from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  fetchNotifications,
  notificationLink,
  type NotificationItem,
} from "@/modules/notifications/api/notifications";
import { PointsIcon } from "@/shared/ui/BrandMark";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  SectionHeading,
  SkeletonBlock,
  StatusBadge,
  SurfaceCard,
} from "@/shared/ui/base";
import { formatDateTime, formatRelative, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const auth = useAuthStore();
const site = useSiteStore();

const pendingLoading = ref(true);
const pendingError = ref("");
const notifications = ref<NotificationItem[]>([]);
const notificationsLoading = ref(true);
const notificationsError = ref("");
const forbidden = ref(false);

const crumbs = [
  { label: "首页", to: "/" },
  { label: "我的账户" },
];

const pendingCount = (key: PendingActionKey): number => auth.pendingActions[key] ?? 0;
const hasAnyPending = computed(() => PENDING_ACTION_META.some((item) => pendingCount(item.key) > 0));

const unreadBadge = computed(() =>
  auth.unread > 0 ? { tone: "amber" as const, label: `${auth.unread} 条未读` } : { tone: "neutral" as const, label: "无未读" },
);

async function loadAccount(): Promise<void> {
  pendingLoading.value = true;
  pendingError.value = "";
  forbidden.value = false;
  try {
    await auth.loadMe();
  } catch (caught) {
    pendingError.value = describeIdentityError(caught);
    forbidden.value = errorCodeOf(caught) === "FORBIDDEN" || (caught instanceof ApiError && caught.status === 403);
  } finally {
    pendingLoading.value = false;
  }
}

async function loadNotifications(): Promise<void> {
  notificationsLoading.value = true;
  notificationsError.value = "";
  try {
    const page = await fetchNotifications({ limit: 5, page: 1 });
    notifications.value = page.items ?? [];
  } catch (caught) {
    notifications.value = [];
    notificationsError.value = describeIdentityError(caught);
  } finally {
    notificationsLoading.value = false;
  }
}

onMounted(() => {
  void site.load();
  void Promise.all([loadAccount(), loadNotifications()]);
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="crumbs" />

    <div class="mt-4 flex flex-wrap items-end justify-between gap-4">
      <SectionHeading
        :level="1"
        eyebrow="Account"
        :title="`你好，${auth.displayName}`"
        description="这里汇总需要你处理的动作。金额、库存与积分以服务端为准，页面上的数字只做展示。"
      />
      <div class="flex flex-wrap items-center gap-3">
        <StatusBadge v-if="auth.user" tone="jade" :label="`已登录 · ${auth.user.phone_masked}`" />
        <StatusBadge :tone="unreadBadge.tone" :label="unreadBadge.label" />
      </div>
    </div>

    <!-- 401/403：不展示对象是否存在 -->
    <EmptyState
      v-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="当前账号无权访问该页面"
      description="请确认登录的账号身份；如需帮助，请通过公开客服邮箱联系，我们不会在此说明其他账号的资料情况。"
    >
      <AppButton size="sm" to="/login">切换账号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else>
      <nav aria-label="账户快捷入口" class="mt-6 flex flex-wrap gap-3">
        <AppButton size="sm" variant="secondary" to="/account/profile">个人资料</AppButton>
        <AppButton size="sm" variant="secondary" to="/account/security">账号与安全</AppButton>
        <AppButton size="sm" variant="secondary" to="/account/addresses">收货地址</AppButton>
        <AppButton size="sm" variant="secondary" to="/account/notifications">通知中心</AppButton>
      </nav>

      <!-- 待办：每块自己的 loading / empty / 深链 -->
      <section class="mt-8" aria-labelledby="pending-heading">
        <h2 id="pending-heading" class="font-display text-xl">需要你处理</h2>

        <p v-if="pendingError" class="mt-3 rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
          {{ pendingError }}
          <AppButton class="ml-3" size="sm" variant="quiet" @click="loadAccount()">重新加载</AppButton>
        </p>

        <div class="mt-4 grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
          <SurfaceCard v-for="item in PENDING_ACTION_META" :key="item.key" class="flex flex-col">
            <SkeletonBlock v-if="pendingLoading" :lines="2" :aria-label="`正在读取${item.label}`" />
            <template v-else>
              <div class="flex items-start justify-between gap-3">
                <h3 class="text-sm font-medium">{{ item.label }}</h3>
                <StatusBadge
                  :tone="pendingCount(item.key) > 0 ? 'amber' : 'neutral'"
                  :label="pendingCount(item.key) > 0 ? `${pendingCount(item.key)} 项` : '无待办'"
                />
              </div>
              <p class="mt-3 flex-1 text-xs leading-relaxed text-ink-muted">{{ item.description }}</p>
              <div class="mt-4">
                <AppButton size="sm" variant="secondary" :to="item.to">查看{{ item.label }}</AppButton>
              </div>
            </template>
          </SurfaceCard>
        </div>

        <p v-if="!pendingLoading && !pendingError && !hasAnyPending" class="mt-3 text-xs text-ink-faint">
          当前没有待处理事项。新的报价、阶段确认或付款提醒会同时出现在通知中心。
        </p>
      </section>

      <!-- 星绒币与通知 -->
      <div class="mt-10 grid gap-6 lg:grid-cols-[0.9fr_1.1fr]">
        <SurfaceCard as="section" aria-labelledby="points-heading">
          <SectionHeading eyebrow="Loyalty" title="星绒币" :level="2" />
          <div class="mt-4 flex items-center gap-2">
            <PointsIcon :size="24" label="星绒币" />
          </div>
          <p class="mt-3 text-sm leading-relaxed text-ink-muted">
            购买奖励按 {{ site.loyaltyRules.denominator_yuan }} 元 = 1 星绒币计算，运费不计入；
            入账延迟 {{ site.loyaltyRules.release_delay_days }} 天，退款会按规则冲回。
          </p>
          <p class="mt-2 text-xs text-ink-faint">
            本页不估算余额：可用、冻结与待入账数值只在星绒币页面读取服务端结果。
          </p>
          <div class="mt-4">
            <AppButton size="sm" variant="secondary" to="/account/points">查看星绒币明细</AppButton>
          </div>
        </SurfaceCard>

        <SurfaceCard as="section" aria-labelledby="notifications-heading">
          <div class="flex flex-wrap items-start justify-between gap-3">
            <h2 id="notifications-heading" class="font-display text-xl">最新通知</h2>
            <AppButton size="sm" variant="secondary" to="/account/notifications">全部通知</AppButton>
          </div>

          <div class="mt-4">
            <SkeletonBlock v-if="notificationsLoading" :lines="4" aria-label="正在读取最新通知" />
            <p
              v-else-if="notificationsError"
              class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine"
              role="alert"
            >
              {{ notificationsError }}
              <AppButton class="ml-3" size="sm" variant="quiet" @click="loadNotifications()">重试</AppButton>
            </p>
            <EmptyState
              v-else-if="notifications.length === 0"
              title="暂时没有通知"
              description="订单状态变化、报价发布与阶段确认会在这里出现，同时按你的通知设置发送邮件。"
            />
            <ul v-else class="divide-y divide-line">
              <li v-for="item in notifications" :key="item.id" class="py-3">
                <div class="flex flex-wrap items-center gap-2">
                  <StatusBadge
                    :tone="item.status === 'UNREAD' ? 'amber' : 'neutral'"
                    :label="item.status === 'UNREAD' ? '未读' : '已读'"
                  />
                  <time class="text-xs tabular-nums text-ink-faint">{{ formatRelative(item.created_at) }}</time>
                </div>
                <p class="mt-1 text-sm font-medium">{{ item.title }}</p>
                <p class="mt-1 text-xs text-ink-muted">{{ truncate(item.body, 96) }}</p>
                <RouterLink
                  v-if="notificationLink(item)"
                  class="link-underline mt-1 inline-block text-xs"
                  :to="notificationLink(item) as string"
                >
                  查看详情
                </RouterLink>
              </li>
            </ul>
          </div>
        </SurfaceCard>
      </div>

      <!-- 账号信息 -->
      <SurfaceCard as="section" class="mt-10">
        <SectionHeading
          eyebrow="Account"
          title="账号信息"
          :level="2"
          description="手机号只以脱敏形式展示；登录、换绑与注销都会记入安全审计。"
        />
        <dl class="mt-5 grid gap-4 text-sm sm:grid-cols-2 lg:grid-cols-4">
          <div>
            <dt class="text-xs text-ink-faint">昵称</dt>
            <dd class="mt-1">{{ auth.user?.nickname ?? "—" }}</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-faint">手机号</dt>
            <dd class="mt-1 tabular-nums">{{ auth.user?.phone_masked ?? "—" }}</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-faint">地区 / 币种</dt>
            <dd class="mt-1">{{ auth.user?.region ?? "—" }} · {{ auth.currency }}</dd>
          </div>
          <div>
            <dt class="text-xs text-ink-faint">账号创建时间</dt>
            <dd class="mt-1 tabular-nums">{{ formatDateTime(auth.user?.created_at ?? null) }}</dd>
          </div>
        </dl>
        <div class="mt-5 flex flex-wrap gap-3">
          <AppButton size="sm" to="/account/profile">编辑昵称与头像</AppButton>
          <AppButton size="sm" variant="secondary" to="/account/security">安全与会话管理</AppButton>
        </div>
      </SurfaceCard>
    </template>
  </div>
</template>
