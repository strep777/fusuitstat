<script setup lang="ts">
/**
 * 站点布局：跳转链接、头部、主体、页脚，以及全局加载与服务端配置载入。
 * 首屏可见内容不等加载动画（11 文档）：配置失败也渲染基线内容。
 */
import { computed, onMounted } from "vue";
import { RouterView, useRoute } from "vue-router";

import SiteFooter from "@/app/components/SiteFooter.vue";
import SiteHeader from "@/app/components/SiteHeader.vue";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";

const route = useRoute();
const site = useSiteStore();
const auth = useAuthStore();

const isPlainLayout = computed(() => route.meta.layout === "plain");

onMounted(async () => {
  await Promise.allSettled([site.load(), auth.loaded ? Promise.resolve(null) : auth.loadMe()]);
});
</script>

<template>
  <a class="skip-link" href="#main-content">跳到主要内容</a>
  <SiteHeader v-if="!isPlainLayout" />
  <main id="main-content" class="min-h-[60vh]">
    <RouterView v-slot="{ Component }">
      <component :is="Component" />
    </RouterView>
  </main>
  <SiteFooter v-if="!isPlainLayout" />
</template>
