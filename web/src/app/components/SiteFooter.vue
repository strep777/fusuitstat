<script setup lang="ts">
/**
 * 站点页脚：品牌墙、经营信息、规则入口、社交渠道与备案位。
 * 只显示后台已配置的 https 外链；没有配置的渠道不输出假入口。
 */
import { computed } from "vue";
import { RouterLink } from "vue-router";

import { useSiteStore } from "@/modules/content/stores/site";
import { useI18n } from "@/shared/lib/i18n";
import { usePreferencesStore } from "@/shared/stores/preferences";
import { BrandMark } from "@/shared/ui/BrandMark";

const site = useSiteStore();
const preferences = usePreferencesStore();
const { t } = useI18n();

const year = new Date().getFullYear();

const legalLinks = computed(() => [
  { label: t("footer.terms"), to: "/legal/terms" },
  { label: t("footer.privacy"), to: "/legal/privacy" },
  { label: t("footer.returns"), to: "/legal/returns" },
  { label: t("footer.points"), to: "/legal/points" },
]);

const helpLinks = computed(() => [
  { label: t("footer.guide"), to: "/commission/guide" },
  { label: t("footer.schedules"), to: "/commission/schedules" },
  { label: t("footer.contract"), to: "/commission/contract" },
  { label: t("footer.faq"), to: "/faq" },
  { label: t("footer.care"), to: "/care" },
]);

const browseLinks = computed(() => [
  { label: t("footer.works"), to: "/works" },
  { label: t("footer.series"), to: "/fursuits" },
  { label: t("footer.shop"), to: "/shop" },
  { label: t("footer.wash"), to: "/wash" },
  { label: t("footer.props"), to: "/props" },
  { label: t("footer.merch"), to: "/merch" },
  { label: t("footer.news"), to: "/news" },
]);

const safeSocial = computed(() =>
  site.socialLinks.filter((link) => /^https:\/\//i.test(link.url.trim())),
);
</script>

<template>
  <footer class="mt-24 border-t border-line bg-[#fafaf8] text-ink">
    <div class="container-page pt-14 sm:pt-20">
      <div class="grid gap-12 pb-14 lg:grid-cols-[1.15fr_1.85fr] lg:gap-20">
        <div>
          <div class="flex items-center gap-3">
            <BrandMark :size="48" decorative />
            <div>
              <p class="font-display text-xl font-medium tracking-tight">{{ site.brand.brand_name }}</p>
              <p class="mt-1 text-[10px] uppercase tracking-[0.18em] text-ink-muted">
                {{ t("common.brandEnglish") }}
              </p>
            </div>
          </div>

          <p class="mt-6 max-w-sm text-sm leading-relaxed text-ink-muted">
            {{ t("footer.intro") }}
          </p>

          <RouterLink
            to="/shop"
            class="mt-7 inline-flex items-center gap-2 rounded-full bg-canvas-sunk px-5 py-2.5 text-sm text-ink transition-colors hover:bg-brand-soft hover:text-brand-strong"
          >
            逛逛商城 <span aria-hidden="true">→</span>
          </RouterLink>

          <dl class="mt-8 grid gap-2.5 text-sm text-ink-muted">
            <div class="flex gap-2">
              <dt class="shrink-0 text-ink-faint">{{ t("footer.legalEntity") }}</dt>
              <dd>{{ site.brand.legal_entity }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-ink-faint">{{ t("footer.supportEmail") }}</dt>
              <dd>
                <a
                  class="underline decoration-line-strong underline-offset-4 hover:text-brand-strong"
                  :href="`mailto:${site.brand.support_email}`"
                >
                  {{ site.brand.support_email }}
                </a>
              </dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-ink-faint">{{ t("footer.serviceHours") }}</dt>
              <dd>{{ site.brand.service_hours }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-ink-faint">{{ t("footer.responseSla") }}</dt>
              <dd>
                {{
                  t("footer.responseSlaValue", {
                    inquiry: site.brand.response_sla.inquiry_workdays,
                    feedback: site.brand.response_sla.feedback_workdays,
                  })
                }}
              </dd>
            </div>
          </dl>
        </div>

        <div class="grid grid-cols-2 gap-x-8 gap-y-10 sm:grid-cols-3">
          <nav :aria-label="t('footer.browse')">
            <h2 class="text-[11px] font-medium tracking-[0.16em] text-ink-faint">{{ t("footer.browse") }}</h2>
            <ul class="mt-4 space-y-2.5 text-sm">
              <li v-for="link in browseLinks" :key="link.to">
                <RouterLink class="text-ink-muted transition-colors hover:text-ink" :to="link.to">
                  {{ link.label }}
                </RouterLink>
              </li>
            </ul>
          </nav>

          <nav :aria-label="t('footer.help')">
            <h2 class="text-[11px] font-medium tracking-[0.16em] text-ink-faint">{{ t("footer.help") }}</h2>
            <ul class="mt-4 space-y-2.5 text-sm">
              <li v-for="link in helpLinks" :key="link.to">
                <RouterLink class="text-ink-muted transition-colors hover:text-ink" :to="link.to">
                  {{ link.label }}
                </RouterLink>
              </li>
            </ul>
          </nav>

          <div class="col-span-2 sm:col-span-1">
            <h2 class="text-[11px] font-medium tracking-[0.16em] text-ink-faint">{{ t("footer.legal") }}</h2>
            <ul class="mt-4 space-y-2.5 text-sm">
              <li v-for="link in legalLinks" :key="link.to">
                <RouterLink class="text-ink-muted transition-colors hover:text-ink" :to="link.to">
                  {{ link.label }}
                </RouterLink>
              </li>
              <li>
                <RouterLink class="text-ink-muted transition-colors hover:text-ink" to="/contact">
                  {{ t("footer.contact") }}
                </RouterLink>
              </li>
              <li>
                <RouterLink class="text-ink-muted transition-colors hover:text-ink" to="/feedback/new">
                  {{ t("footer.feedback") }}
                </RouterLink>
              </li>
            </ul>

            <div v-if="safeSocial.length" class="mt-6">
              <h2 class="text-[11px] font-medium tracking-[0.16em] text-ink-faint">{{ t("footer.social") }}</h2>
              <ul class="mt-3 flex flex-wrap gap-2 text-xs">
                <li v-for="link in safeSocial" :key="link.code">
                  <a
                    class="inline-block rounded-full bg-surface-muted px-3 py-1.5 text-ink-muted transition-colors hover:bg-brand-soft hover:text-brand-strong"
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
      </div>

      <div class="flex flex-col gap-4 border-t border-line py-6 text-xs text-ink-faint sm:flex-row sm:items-center sm:justify-between">
        <p>
          © {{ year }} {{ site.brand.legal_entity }} · {{ site.brand.brand_name }} · {{ site.brand.domain }}
        </p>
        <div class="flex flex-wrap items-center gap-4">
          <label class="inline-flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              class="size-4 accent-brand"
              :checked="preferences.reducedMotion"
              @change="preferences.setReducedMotion(($event.target as HTMLInputElement).checked)"
            />
            {{ t("footer.reduceMotion") }}
          </label>
          <button
            type="button"
            class="underline decoration-line-strong underline-offset-4 hover:text-ink"
            @click="preferences.setTheme(preferences.theme === 'dark' ? 'light' : 'dark')"
          >
            {{ preferences.theme === "dark" ? t("footer.themeToLight") : t("footer.themeToDark") }}
          </button>
        </div>
      </div>
      <p v-if="site.error" class="pb-4 text-xs text-wine" role="status">
        {{ t("footer.configFallback") }}
      </p>

      <p
        class="select-none overflow-hidden pt-2 font-display text-[clamp(3rem,10vw,8rem)] font-medium leading-[0.82] tracking-[-0.05em] text-ink/5"
        aria-hidden="true"
      >
        STARIUM
      </p>
    </div>
  </footer>
</template>
