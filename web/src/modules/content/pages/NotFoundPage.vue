<script setup lang="ts">
/**
 * 404 页面（03 文档：真实 404 语义，不自动跳转）。
 *
 * 路由 `:pathMatch(.*)*` 声明为 `not-found`，robots 由 router.afterEach 设为
 * `noindex, follow`；静态托管/预渲染层用同一路由返回 404 状态码。
 * 页面给出三条出口：首页、作品集、联系与反馈，不做定时跳转，也不伪装成 200 的内容页。
 */
import { computed, onMounted } from "vue";
import { RouterLink, useRoute } from "vue-router";

import { applyPageHead } from "@/modules/content/api/content";
import { AppButton, Breadcrumbs, SectionHeading, SurfaceCard } from "@/shared/ui/base";

const route = useRoute();

const requestedPath = computed(() => {
  const value = route.fullPath || "/";
  return value.length > 120 ? `${value.slice(0, 119)}…` : value;
});

const exits = [
  {
    title: "返回首页",
    description: "从首页的档期状态与代表作品重新开始。",
    to: "/",
    label: "回到首页",
  },
  {
    title: "浏览作品集",
    description: "按类型、系列与物种筛选已授权公开的成品。",
    to: "/works",
    label: "打开作品集",
  },
  {
    title: "联系与反馈",
    description: "地址来自旧链接或需要人工协助时，直接找客服。",
    to: "/contact",
    label: "联系客服",
  },
];

const suggestions = [
  { label: "公告", to: "/news" },
  { label: "系列与规格", to: "/fursuits" },
  { label: "委托指南", to: "/commission/guide" },
  { label: "常见问题", to: "/faq" },
  { label: "养护与清洁", to: "/care" },
  { label: "规则与条款", to: "/legal/terms" },
];

onMounted(() => {
  // 错误页不写 canonical，也不输出 OG，只声明 noindex
  applyPageHead({
    title: "页面不存在",
    description: "没有找到这个地址对应的页面。可以从首页、作品集或联系页面继续。",
    robots: "noindex, follow",
  });
});
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="[{ label: '首页', to: '/' }, { label: '页面不存在' }]" />
    </div>

    <section class="container-page pb-10 pt-8">
      <SectionHeading
        eyebrow="404"
        level="1"
        title="这个地址没有对应页面"
        description="地址可能拼写有误、内容已经下架，或者这条链接来自旧版本。下架的内容不再公开，也不会用其它作品冒充。"
      />
      <p class="mt-4 text-sm text-ink-muted">
        请求的地址：<span class="font-mono text-xs text-ink-soft">{{ requestedPath }}</span>
      </p>
    </section>

    <section class="container-page pb-12">
      <div class="grid gap-6 md:grid-cols-3">
        <SurfaceCard v-for="exit in exits" :key="exit.to" class="flex h-full flex-col">
          <h2 class="font-display text-lg">{{ exit.title }}</h2>
          <p class="mt-3 flex-1 text-sm leading-relaxed text-ink-muted">{{ exit.description }}</p>
          <div class="mt-5">
            <AppButton :to="exit.to" size="sm" variant="secondary">{{ exit.label }}</AppButton>
          </div>
        </SurfaceCard>
      </div>
    </section>

    <section class="border-t border-line bg-surface-muted">
      <div class="container-page py-12">
        <h2 class="font-display text-xl">也可能你想找这些页面</h2>
        <ul class="mt-5 flex flex-wrap gap-3">
          <li v-for="item in suggestions" :key="item.to">
            <RouterLink
              class="inline-flex min-h-[44px] items-center rounded-full bg-canvas-sunk px-5 text-sm text-ink-soft transition-colors hover:bg-brand-soft hover:text-brand-strong"
              :to="item.to"
            >
              {{ item.label }}
            </RouterLink>
          </li>
        </ul>
        <p class="mt-6 text-xs text-ink-faint">
          如果你确定这个地址以前可以打开，请把地址和大致时间发给客服，我们会核对是否下架或改名。
        </p>
      </div>
    </section>
  </div>
</template>
