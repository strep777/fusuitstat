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
  <footer class="mt-24 border-t-2 border-ink bg-[#17121C] text-[#FFFCF2]">
    <div class="container-page pt-12 sm:pt-16">
      <div class="grid gap-10 border-b-2 border-white/15 pb-10 lg:grid-cols-[1.1fr_1.9fr] lg:gap-16">
        <div>
          <div class="flex items-center gap-3">
            <BrandMark :size="52" variant="light" decorative />
            <div>
              <p class="font-display text-2xl font-bold tracking-tight">{{ site.brand.brand_name }}</p>
              <p class="mt-1 font-mono text-[10px] uppercase tracking-[0.18em] text-white/50">
                {{ t("common.brandEnglish") }}
              </p>
            </div>
          </div>
          <p class="mt-5 max-w-sm text-sm leading-relaxed text-white/65">
            {{ t("footer.intro") }}
          </p>

          <RouterLink
            to="/shop"
            class="mt-6 inline-flex items-center gap-3 border-2 border-[#FFFCF2] bg-lemon px-5 py-3 font-display text-lg font-bold text-[#17121C] shadow-[5px_5px_0_#FFFCF2] transition-all hover:-translate-y-0.5 hover:shadow-[7px_7px_0_#FFFCF2]"
          >
            逛逛商城 <span aria-hidden="true">↗</span>
          </RouterLink>

          <dl class="mt-8 grid gap-2 text-sm text-white/65">
            <div class="flex gap-2">
              <dt class="shrink-0 text-white/40">{{ t("footer.legalEntity") }}</dt>
              <dd>{{ site.brand.legal_entity }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-white/40">{{ t("footer.supportEmail") }}</dt>
              <dd>
                <a class="underline decoration-white/30 underline-offset-4 hover:text-lemon" :href="`mailto:${site.brand.support_email}`">
                  {{ site.brand.support_email }}
                </a>
              </dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-white/40">{{ t("footer.serviceHours") }}</dt>
              <dd>{{ site.brand.service_hours }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-white/40">{{ t("footer.responseSla") }}</dt>
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

        <div class="grid grid-cols-2 gap-8 sm:grid-cols-3">
          <nav :aria-label="t('footer.browse')">
            <h2 class="font-mono text-[11px] uppercase tracking-[0.18em] text-lemon">01 / {{ t("footer.browse") }}</h2>
            <ul class="mt-4 space-y-2.5 text-sm">
              <li v-for="link in browseLinks" :key="link.to">
                <RouterLink class="text-white/70 transition-colors hover:text-lemon" :to="link.to">
                  {{ link.label }}
                </RouterLink>
              </li>
            </ul>
          </nav>

          <nav :aria-label="t('footer.help')">
            <h2 class="font-mono text-[11px] uppercase tracking-[0.18em] text-lemon">02 / {{ t("footer.help") }}</h2>
            <ul class="mt-4 space-y-2.5 text-sm">
              <li v-for="link in helpLinks" :key="link.to">
                <RouterLink class="text-white/70 transition-colors hover:text-lemon" :to="link.to">
                  {{ link.label }}
                </RouterLink>
              </li>
            </ul>
          </nav>

          <div class="col-span-2 sm:col-span-1">
            <h2 class="font-mono text-[11px] uppercase tracking-[0.18em] text-lemon">03 / {{ t("footer.legal") }}</h2>
            <ul class="mt-4 space-y-2.5 text-sm">
              <li v-for="link in legalLinks" :key="link.to">
                <RouterLink class="text-white/70 transition-colors hover:text-lemon" :to="link.to">
                  {{ link.label }}
                </RouterLink>
              </li>
              <li>
                <RouterLink class="text-white/70 transition-colors hover:text-lemon" to="/contact">
                  {{ t("footer.contact") }}
                </RouterLink>
              </li>
              <li>
                <RouterLink class="text-white/70 transition-colors hover:text-lemon" to="/feedback/new">
                  {{ t("footer.feedback") }}
                </RouterLink>
              </li>
            </ul>

            <div v-if="safeSocial.length" class="mt-6">
              <h2 class="font-mono text-[11px] uppercase tracking-[0.18em] text-white/45">{{ t("footer.social") }}</h2>
              <ul class="mt-3 flex flex-wrap gap-2 text-xs">
                <li v-for="link in safeSocial" :key="link.code">
                  <a
                    class="inline-block border-2 border-white/25 px-2.5 py-1 text-white/70 transition-colors hover:border-lemon hover:text-lemon"
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

      <div class="mt-6 flex flex-col gap-4 text-xs text-white/45 sm:flex-row sm:items-center sm:justify-between">
        <p>
          © {{ year }} {{ site.brand.legal_entity }} · {{ site.brand.brand_name }} · {{ site.brand.domain }}
        </p>
        <div class="flex flex-wrap items-center gap-4">
          <label class="inline-flex cursor-pointer items-center gap-2">
            <input
              type="checkbox"
              class="size-4 accent-lemon"
              :checked="preferences.reducedMotion"
              @change="preferences.setReducedMotion(($event.target as HTMLInputElement).checked)"
            />
            {{ t("footer.reduceMotion") }}
          </label>
          <button
            type="button"
            class="underline decoration-white/30 underline-offset-4 hover:text-lemon"
            @click="preferences.setTheme(preferences.theme === 'dark' ? 'light' : 'dark')"
          >
            {{ preferences.theme === "dark" ? t("footer.themeToLight") : t("footer.themeToDark") }}
          </button>
        </div>
      </div>
      <p v-if="site.error" class="mt-3 text-xs text-wine-soft" role="status">
        {{ t("footer.configFallback") }}
      </p>

      <p
        class="mt-8 select-none overflow-hidden border-t-2 border-white/15 pt-4 font-display text-[clamp(3rem,11vw,9rem)] font-bold leading-[0.8] tracking-[-0.05em] text-white/10"
        aria-hidden="true"
      >
        STARIUM
      </p>
    </div>
  </footer>
</template>
