<script setup lang="ts">
/**
 * 渠道返回页 `/payment/return`。
 *
 * 规则（08 文档 / 03 文档）：
 * - 渠道跳回后不读取前端 success 之类的参数判定成交，页面只引导用户登录并核对订单。
 * - 成交只认服务端已验签通知或主动查单结果；本页不设置任何 OG 元信息，也不输出支付明细。
 * - 访客也能打开本页，未登录时引导登录；登录后默认进入订单列表自行核对。
 */
import { onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SurfaceCard } from "@/shared/ui/base";

const route = useRoute();
const auth = useAuthStore();
const site = useSiteStore();

const loading = ref(true);
const loginError = ref(false);

/**
 * 只允许本站相对路径作为返回目标；渠道附加的 success / trade_status 等参数一律丢弃，
 * 避免把渠道回传参数当作成交证据或带到后续页面。
 */
const safeOrderHint = ref("");

onMounted(async () => {
  const raw = String(route.query["order"] ?? "");
  safeOrderHint.value = /^[A-Za-z0-9_-]{1,64}$/.test(raw) ? raw : "";
  await Promise.allSettled([site.load()]);
  if (!auth.loaded) {
    try {
      await auth.loadMe();
    } catch {
      loginError.value = true;
    }
  }
  loading.value = false;
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '支付结果核对' }]" />

    <SectionHeading
      class="mt-6"
      :level="1"
      eyebrow="Payment return"
      title="支付结果核对"
      description="你已从支付渠道返回本站。是否付款成功由服务端通知或查单确认，本页不会因为浏览器地址栏带了任何参数就判定成交。"
    />

    <div class="mt-8 grid gap-6 lg:grid-cols-[1.4fr_1fr] lg:items-start">
      <div class="space-y-6">
        <div class="rounded-lg border border-amber bg-amber-soft p-5 text-sm text-amber-strong">
          <p class="font-medium">请以订单详情里的支付记录为准</p>
          <p class="mt-1">
            渠道返回时地址里可能带有 success、trade_status 之类的参数，这些参数由浏览器传递、可以被人为修改，
            因此本页不读取它们，也不会据此显示“付款成功”。只有服务端收到已验签的支付通知，或主动向渠道查单确认后，
            才会把那笔支付记为已确认收款。
          </p>
        </div>

        <SurfaceCard v-if="loading">
          <p class="text-sm text-ink-muted" role="status">正在确认登录状态…</p>
        </SurfaceCard>

        <!-- 已登录：直接核对订单 -->
        <SurfaceCard v-else-if="auth.isAuthenticated">
          <h2 class="font-display text-lg">下一步：核对你的订单</h2>
          <p class="mt-3 text-sm text-ink-muted">
            已登录为 {{ auth.displayName }}。请打开订单详情，查看应付项的支付记录：已确认收款会显示查单确认时间；
            显示“结果确认中”表示服务端还没有拿到渠道结果，这不等于付款失败，也不等于订单已取消。
          </p>
          <div class="mt-5 flex flex-wrap gap-3">
            <AppButton
              v-if="safeOrderHint"
              size="sm"
              :to="`/account/orders/${safeOrderHint}`"
            >
              打开该订单
            </AppButton>
            <AppButton size="sm" :variant="safeOrderHint ? 'secondary' : 'primary'" to="/account/orders">
              查看我的订单
            </AppButton>
            <AppButton size="sm" variant="secondary" to="/account/after-sales">售后与退款</AppButton>
          </div>
          <p class="mt-3 text-xs text-ink-faint">
            找不到订单时不要重复付款：先刷新订单列表，或联系客服核对在途支付记录。
          </p>
        </SurfaceCard>

        <!-- 未登录：引导登录核对 -->
        <EmptyState
          v-else
          tone="empty"
          title="登录后核对订单"
          description="订单与支付记录只对本人可见。登录后即可看到该笔订单的权威状态；未登录时本页不展示任何订单或支付信息。"
        >
          <AppButton size="sm" :to="`/login?returnTo=${encodeURIComponent('/account/orders')}`">手机号登录</AppButton>
          <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
        </EmptyState>

        <p v-if="loginError" class="rounded-sm bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
          暂时无法确认登录状态，可能是网络问题。可以直接打开登录页重新登录后核对订单。
        </p>

        <SurfaceCard>
          <h2 class="font-display text-lg">常见情况说明</h2>
          <dl class="mt-3 space-y-3 text-sm">
            <div>
              <dt class="font-medium text-ink-soft">渠道页面显示已支付，但订单仍是待付款</dt>
              <dd class="mt-1 text-ink-muted">
                通知可能还在路上。服务端会主动查单核实，请稍后在订单详情点「立即查询付款结果」，不要重复付款。
              </dd>
            </div>
            <div>
              <dt class="font-medium text-ink-soft">支付页显示结果确认中</dt>
              <dd class="mt-1 text-ink-muted">
                表示查单尚未返回明确结果。服务端会按退避策略继续查询；这既不是失败，也不是订单已取消。
              </dd>
            </div>
            <div>
              <dt class="font-medium text-ink-soft">付款窗口已到期</dt>
              <dd class="mt-1 text-ink-muted">
                到期只影响继续支付的窗口，订单是否关闭由服务端在核对没有在途成功付款后决定。
              </dd>
            </div>
            <div>
              <dt class="font-medium text-ink-soft">重复付款或缺货</dt>
              <dd class="mt-1 text-ink-muted">
                重复收款会进入多付处置流程并触发退款；退款按原渠道退回原付款来源。
                需要人工核对请联系
                <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
                （{{ site.brand.service_hours }}）。
              </dd>
            </div>
          </dl>
        </SurfaceCard>
      </div>

      <div class="space-y-4 lg:sticky lg:top-24">
        <SurfaceCard>
          <h3 class="font-display text-base">本页不做什么</h3>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>不读取地址栏的 success / status 等参数判定成交。</li>
            <li>不生成“支付成功”结果页，也不展示支付渠道的原始报文。</li>
            <li>不展示订单号、金额、地址等私密信息（未登录时一律不展示）。</li>
            <li>不改动订单状态；状态只能由服务端通知或查单确认写入。</li>
          </ul>
        </SurfaceCard>

        <SurfaceCard>
          <h3 class="font-display text-base">需要帮忙？</h3>
          <p class="mt-3 text-sm text-ink-muted">
            客服邮箱
            <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>，
            工作时段 {{ site.brand.service_hours }}，反馈首响
            {{ site.brand.response_sla.feedback_workdays }} 个工作日。
          </p>
          <div class="mt-4 flex flex-wrap gap-3">
            <AppButton size="sm" variant="secondary" to="/contact">联系与反馈</AppButton>
            <AppButton size="sm" variant="ghost" to="/faq">常见问题</AppButton>
          </div>
        </SurfaceCard>

        <p class="rounded-lg border border-line bg-surface-muted p-4 text-xs text-ink-muted">
          本页不会被搜索引擎收录，也不预渲染，以免泄露个人交易信息。你也可以直接访问
          <a class="link-underline" href="/account/orders">我的订单</a>
          核对状态。
        </p>
      </div>
    </div>
  </div>
</template>
