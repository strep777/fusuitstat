<script setup lang="ts">
/**
 * 站点页脚：经营信息、规则入口、社交渠道与备案位。
 * 只显示后台已配置的安全协议外链；没有配置的渠道不输出假入口。
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";

import { useSiteStore } from "@/modules/content/stores/site";
import { usePreferencesStore } from "@/shared/stores/preferences";
import { BrandMark } from "@/shared/ui/BrandMark";

const site = useSiteStore();
const preferences = usePreferencesStore();

const year = new Date().getFullYear();

const legalLinks = [
  { label: "用户服务协议", to: "/legal/terms" },
  { label: "隐私政策", to: "/legal/privacy" },
  { label: "取消与退换规则", to: "/legal/returns" },
  { label: "星绒币规则", to: "/legal/points" },
];

const helpLinks = [
  { label: "委托指南", to: "/commission/guide" },
  { label: "档期与名额", to: "/commission/schedules" },
  { label: "合同示例", to: "/commission/contract" },
  { label: "常见问题", to: "/faq" },
  { label: "养护与清洁", to: "/care" },
];

const browseLinks = [
  { label: "作品集", to: "/works" },
  { label: "系列规格", to: "/fursuits" },
  { label: "角色领养", to: "/characters" },
  { label: "商城", to: "/shop" },
  { label: "公告", to: "/news" },
];

const safeSocial = computed(() =>
  site.socialLinks.filter((link) => /^https:\/\//i.test(link.url.trim())),
);
</script>

<template>
  <footer class="mt-20 border-t border-night-line bg-night text-canvas">
    <div class="container-page py-14">
      <div class="grid gap-10 lg:grid-cols-[1.3fr_1fr_1fr_1fr]">
        <div>
          <div class="flex items-center gap-3">
            <BrandMark :size="48" variant="light" decorative />
            <div>
              <p class="font-display text-lg">{{ site.brand.brand_name }}</p>
              <p class="text-xs text-canvas/60">绒里有星，装配成纪</p>
            </div>
          </div>
          <p class="mt-5 max-w-sm text-sm leading-relaxed text-canvas/70">
            手工制作的全装、半装、单头与配件定制工作室。委托先询价、再报价、签合同后进入排期，全程可在账户内查看进度。
          </p>
          <dl class="mt-6 space-y-2 text-sm text-canvas/70">
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">经营主体</dt>
              <dd>{{ site.brand.legal_entity }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">客服邮箱</dt>
              <dd>
                <a class="link-underline" :href="`mailto:${site.brand.support_email}`">
                  {{ site.brand.support_email }}
                </a>
              </dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">工作时段</dt>
              <dd>{{ site.brand.service_hours }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">人工响应</dt>
              <dd>
                询价 {{ site.brand.response_sla.inquiry_workdays }} 个工作日、反馈
                {{ site.brand.response_sla.feedback_workdays }} 个工作日首响
              </dd>
            </div>
          </dl>
        </div>

        <nav aria-label="浏览">
          <h2 class="eyebrow text-canvas/50">浏览</h2>
          <ul class="mt-4 space-y-2 text-sm">
            <li v-for="link in browseLinks" :key="link.to">
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" :to="link.to">
                {{ link.label }}
              </RouterLink>
            </li>
          </ul>
        </nav>

        <nav aria-label="委托与帮助">
          <h2 class="eyebrow text-canvas/50">委托与帮助</h2>
          <ul class="mt-4 space-y-2 text-sm">
            <li v-for="link in helpLinks" :key="link.to">
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" :to="link.to">
                {{ link.label }}
              </RouterLink>
            </li>
          </ul>
        </nav>

        <div>
          <h2 class="eyebrow text-canvas/50">规则与联系</h2>
          <ul class="mt-4 space-y-2 text-sm">
            <li v-for="link in legalLinks" :key="link.to">
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" :to="link.to">
                {{ link.label }}
              </RouterLink>
            </li>
            <li>
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" to="/contact">
                联系与反馈
              </RouterLink>
            </li>
            <li>
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" to="/feedback/new">
                提交问题或建议
              </RouterLink>
            </li>
          </ul>

          <div v-if="safeSocial.length" class="mt-6">
            <h2 class="eyebrow text-canvas/50">社交渠道</h2>
            <ul class="mt-3 flex flex-wrap gap-2 text-xs">
              <li v-for="link in safeSocial" :key="link.code">
                <a
                  class="inline-block rounded-full border border-night-line px-3 py-1.5 text-canvas/75 transition-colors hover:border-amber-ink hover:text-amber-ink"
                  :href="link.url"
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  {{ link.label }}
                </a>
              </li>
            </ul>
          </div>
        </div>
      </div>

      <div class="mt-12 flex flex-col gap-4 border-t border-night-line pt-6 text-xs text-canvas/50 sm:flex-row sm:items-center sm:justify-between">
        <p>
          © {{ year }} {{ site.brand.legal_entity }} · {{ site.brand.brand_name }} · {{ site.brand.domain }}
        </p>
        <div class="flex flex-wrap items-center gap-4">
          <label class="inline-flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              class="size-4 accent-amber"
              :checked="preferences.reducedMotion"
              @change="preferences.setReducedMotion(($event.target as HTMLInputElement).checked)"
            />
            减弱动效
          </label>
          <button
            type="button"
            class="link-underline"
            @click="preferences.setTheme(preferences.theme === 'dark' ? 'light' : 'dark')"
          >
            {{ preferences.theme === "dark" ? "切换浅色主题" : "切换深色主题" }}
          </button>
        </div>
      </div>
      <p v-if="site.error" class="mt-4 text-xs text-wine-soft" role="status">
        站点配置未能从服务端加载，当前显示的是文档基线值。部分运营信息可能不是最新。
      </p>
    </div>
  </footer>
</template>
