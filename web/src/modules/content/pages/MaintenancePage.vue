<script setup lang="ts">
/**
 * 维护页（03 文档：维护时保留支付查单和客服路径）。
 *
 * 路由使用 plain 布局，页面自带品牌标识与出口，不依赖站点头尾。
 * 文案必须说清：维护期间不能下单与付款；已经付款的订单仍然可以在支付结果核对页查询状态，
 * 客服邮箱照常受理问题。不写预计恢复时间的猜测值——没有核准时间就不写。
 */
import { onMounted } from "vue";
import { RouterLink } from "vue-router";

import { applyPageHead } from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { BrandMark } from "@/shared/ui/BrandMark";
import { AppButton, SurfaceCard } from "@/shared/ui/base";

const site = useSiteStore();

onMounted(() => {
  applyPageHead({
    title: "维护中",
    description: "站点正在维护。已经付款的订单仍可以查询支付结果，客服邮箱照常受理问题。",
    robots: "noindex, nofollow",
  });
  void site.load();
});
</script>

<template>
  <div class="flex min-h-screen flex-col bg-canvas">
    <div class="container-page flex flex-1 flex-col justify-center py-16">
      <div class="mx-auto w-full max-w-2xl">
        <div class="flex items-center gap-3">
          <BrandMark :size="48" variant="dark" decorative />
          <div>
            <p class="eyebrow">{{ site.brand.brand_name }}</p>
            <p class="font-display text-lg">绒里有星，装配成纪</p>
          </div>
        </div>

        <h1 class="mt-8 font-display text-3xl leading-tight sm:text-4xl">站点正在维护</h1>
        <p class="mt-4 text-sm leading-relaxed text-ink-muted">
          部分功能暂时不可用：下单、付款、提交询价与登录都可能失败。已经付款的订单不受影响，
          可以在“支付结果核对”页查询状态；客服邮箱照常有人查看。
          恢复时间以公告为准，这里不写预计时间的猜测值。
        </p>

        <div class="mt-8 flex flex-wrap gap-3">
          <AppButton to="/payment/return" size="lg">查询支付结果</AppButton>
          <a
            class="inline-flex min-h-[52px] items-center rounded-sm border border-line-strong bg-surface px-7 text-base text-ink transition-colors hover:border-amber hover:text-amber-strong"
            :href="`mailto:${site.brand.support_email}`"
          >
            发邮件给客服
          </a>
        </div>

        <div class="mt-8 grid gap-4 sm:grid-cols-2">
          <SurfaceCard as="div">
            <h2 class="font-display text-base">已经付款的订单</h2>
            <p class="mt-2 text-sm text-ink-muted">
              支付结果核对页按订单号查询服务端的权威状态，不会因为维护而把已付款订单显示成未付款。
              如果状态显示“结果尚未确认”，请稍后重试，不要重复付款。
            </p>
            <p class="mt-4 text-sm">
              <RouterLink class="link-underline hover:text-amber-strong" to="/payment/return">
                打开支付结果核对
              </RouterLink>
            </p>
          </SurfaceCard>

          <SurfaceCard as="div">
            <h2 class="font-display text-base">联系客服</h2>
            <p class="mt-2 text-sm text-ink-muted">
              工作时段：{{ site.brand.service_hours }}。反馈与投诉也可以直接发邮件，不需要登录。
            </p>
            <p class="mt-4 text-sm">
              <a class="link-underline hover:text-amber-strong" :href="`mailto:${site.brand.support_email}`">
                {{ site.brand.support_email }}
              </a>
            </p>
          </SurfaceCard>
        </div>

        <dl class="mt-10 grid gap-4 border-t border-line pt-6 text-xs text-ink-faint sm:grid-cols-2">
          <div>
            <dt>经营主体</dt>
            <dd class="mt-1 text-ink-muted">{{ site.brand.legal_entity }}</dd>
          </div>
          <div>
            <dt>站点域名</dt>
            <dd class="mt-1 text-ink-muted">{{ site.brand.domain }}</dd>
          </div>
        </dl>
      </div>
    </div>
  </div>
</template>
