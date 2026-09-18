<script setup lang="ts">
/**
 * 兑换（C15 默认关闭）。
 *
 * 规则源：文档/09（若启用兑换或抵扣）、文档/03（`/rewards` 仅在开关启用后注册）。
 *
 * 关键约束：
 * - `redemption_enabled` 为 false 时只说明“兑换尚未开放”，不展示任何权益、商品或兑换率。
 * - 不显示假商品、不写“即将上线”式的诱导文案、不预告具体权益。
 * - 开关打开后也只展示服务端配置里真实存在的说明，没有配置时不编造。
 */
import { computed, onMounted } from "vue";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { POINTS_PROHIBITION_NOTICE } from "@/modules/loyalty/api/loyalty";
import { useDocumentTitle } from "@/modules/commissions/api/commissions";
import { AppButton, Breadcrumbs, EmptyState, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { PointsIcon } from "@/shared/ui/BrandMark";

const site = useSiteStore();
const auth = useAuthStore();
const setTitle = useDocumentTitle();

onMounted(async () => {
  setTitle("星绒币兑换");
  void site.load();
  if (!auth.loaded) {
    await auth.loadMe().catch(() => null);
  }
});

const rules = computed(() => site.loyaltyRules);
const enabled = computed(() => rules.value.redemption_enabled === true);

/** 只有服务端给出真实说明时才展示；没有就不编造兑换率或商品范围。 */
const ratioNote = computed(() => (rules.value.redemption_ratio_note ?? "").trim());
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '星绒币兑换' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <div class="flex flex-wrap items-center gap-3">
        <p class="eyebrow">Rewards</p>
        <StatusBadge :tone="enabled ? 'jade' : 'wine'" :label="enabled ? '已启用' : '尚未开放'" />
      </div>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">星绒币兑换</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        兑换属于可选扩展，只有在规则确认、接口与结算链路都准备好之后才会开放。
      </p>
    </header>

    <!-- 配置没读到 ≠ 功能未开放：兑换开关来自服务端配置，读不到时必须说明「不知道」，
         不能直接断言「兑换尚未开放」（03 文档：数据页必须有 loading/error）。 -->
    <section v-if="site.loading && !site.loaded" class="mt-8" aria-live="polite">
      <SkeletonBlock :lines="3" aria-label="正在读取星绒币规则" />
    </section>
    <EmptyState
      v-else-if="site.error"
      class="mt-8"
      tone="error"
      title="星绒币规则暂时无法加载"
      :description="`${site.error} 兑换是否开放来自服务端配置，读到之前页面不会断言「未开放」，也不展示任何兑换率。`"
    >
      <AppButton size="sm" @click="site.load(true)">重新加载规则</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/points">查看星绒币与流水</AppButton>
    </EmptyState>

    <!-- 未启用：只说明未开放 -->
    <template v-else-if="!enabled">
      <SurfaceCard class="mt-8">
        <div class="flex items-start gap-3">
          <PointsIcon :size="32" />
          <div>
            <h2 class="font-display text-lg">兑换尚未开放</h2>
            <p class="mt-3 text-sm leading-relaxed text-ink-muted">
              当前没有可以兑换的权益，也没有可以抵扣的金额。这个页面不会展示未上线的权益或占位商品，
              你看到的星绒币余额仍然会完整保留在你的账户里。
            </p>
          </div>
        </div>

        <dl class="mt-6 divide-y divide-line text-sm">
          <div class="flex flex-wrap justify-between gap-3 py-3">
            <dt class="text-ink-faint">现在能做什么</dt>
            <dd class="text-ink-soft">查看余额、流水、获得方式与规则，并对账目提出申诉。</dd>
          </div>
          <div class="flex flex-wrap justify-between gap-3 py-3">
            <dt class="text-ink-faint">现在不能做什么</dt>
            <dd class="text-ink-soft">不能兑换、不能抵扣、不能提现、不能转账。</dd>
          </div>
          <div class="flex flex-wrap justify-between gap-3 py-3">
            <dt class="text-ink-faint">开放时会说明什么</dt>
            <dd class="text-ink-soft">
              商品范围、兑换率、最低消费、抵扣比例上限、有效期与取消返还规则；确认前不会出现在页面上。
            </dd>
          </div>
        </dl>

        <p class="mt-5 rounded-sm bg-wine-soft px-4 py-3 text-xs leading-relaxed text-wine">
          {{ POINTS_PROHIBITION_NOTICE }}
        </p>

        <div class="mt-6 flex flex-wrap gap-3">
          <AppButton to="/account/points">查看星绒币与流水</AppButton>
          <AppButton variant="secondary" to="/feedback/new">提交反馈</AppButton>
          <AppButton variant="ghost" to="/contact">联系客服</AppButton>
        </div>
      </SurfaceCard>
    </template>

    <!-- 已启用：只展示真实配置 -->
    <template v-else>
      <SurfaceCard class="mt-8">
        <h2 class="font-display text-lg">兑换规则</h2>
        <p v-if="ratioNote" class="mt-3 text-sm leading-relaxed text-ink-muted">{{ ratioNote }}</p>
        <p v-else class="mt-3 text-sm leading-relaxed text-ink-muted">
          兑换已启用，但本站还没有发布可兑换的具体权益清单。请以公告为准；在权益清单发布前，
          页面不会展示任何可兑换项目。
        </p>

        <dl class="mt-6 divide-y divide-line text-sm">
          <div class="flex flex-wrap justify-between gap-3 py-3">
            <dt class="text-ink-faint">换算基准</dt>
            <dd class="text-ink-soft">
              获得：每 {{ rules.denominator_yuan }} 元商品实付金额 = 1 星绒币
            </dd>
          </div>
          <div class="flex flex-wrap justify-between gap-3 py-3">
            <dt class="text-ink-faint">到账时间</dt>
            <dd class="text-ink-soft">
              订单完成交付后 {{ rules.release_delay_days }} 天且无未决售后入账
            </dd>
          </div>
          <div class="flex flex-wrap justify-between gap-3 py-3">
            <dt class="text-ink-faint">明确不支持</dt>
            <dd class="text-ink-soft">充值、提现、转账，以及用星绒币替代现金退款</dd>
          </div>
        </dl>

        <div class="mt-6 flex flex-wrap gap-3">
          <AppButton to="/account/points">查看星绒币与流水</AppButton>
          <AppButton variant="secondary" to="/shop">去商城</AppButton>
        </div>
      </SurfaceCard>
    </template>

    <EmptyState
      v-if="!auth.isAuthenticated"
      class="mt-8"
      tone="forbidden"
      title="登录后可以查看自己的星绒币"
      description="兑换与余额只对本人开放。登录后会回到这一页。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
    </EmptyState>
  </div>
</template>
