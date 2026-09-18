<script setup lang="ts">
/**
 * 站点页脚：经营信息、规则入口、社交渠道与备案位。
 * 只显示后台已配置的安全协议外链；没有配置的渠道不输出假入口。
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
  { label: t("footer.characters"), to: "/characters" },
  { label: t("footer.shop"), to: "/shop" },
  { label: t("footer.news"), to: "/news" },
]);

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
              <p class="text-xs text-canvas/60">{{ t("common.brandTagline") }}</p>
            </div>
          </div>
          <p class="mt-5 max-w-sm text-sm leading-relaxed text-canvas/70">
            {{ t("footer.intro") }}
          </p>
          <dl class="mt-6 space-y-2 text-sm text-canvas/70">
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">{{ t("footer.legalEntity") }}</dt>
              <dd>{{ site.brand.legal_entity }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">{{ t("footer.supportEmail") }}</dt>
              <dd>
                <a class="link-underline" :href="`mailto:${site.brand.support_email}`">
                  {{ site.brand.support_email }}
                </a>
              </dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">{{ t("footer.serviceHours") }}</dt>
              <dd>{{ site.brand.service_hours }}</dd>
            </div>
            <div class="flex gap-2">
              <dt class="shrink-0 text-canvas/50">{{ t("footer.responseSla") }}</dt>
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

        <nav :aria-label="t('footer.browse')">
          <h2 class="eyebrow text-canvas/50">{{ t("footer.browse") }}</h2>
          <ul class="mt-4 space-y-2 text-sm">
            <li v-for="link in browseLinks" :key="link.to">
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" :to="link.to">
                {{ link.label }}
              </RouterLink>
            </li>
          </ul>
        </nav>

        <nav :aria-label="t('footer.help')">
          <h2 class="eyebrow text-canvas/50">{{ t("footer.help") }}</h2>
          <ul class="mt-4 space-y-2 text-sm">
            <li v-for="link in helpLinks" :key="link.to">
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" :to="link.to">
                {{ link.label }}
              </RouterLink>
            </li>
          </ul>
        </nav>

        <div>
          <h2 class="eyebrow text-canvas/50">{{ t("footer.legal") }}</h2>
          <ul class="mt-4 space-y-2 text-sm">
            <li v-for="link in legalLinks" :key="link.to">
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" :to="link.to">
                {{ link.label }}
              </RouterLink>
            </li>
            <li>
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" to="/contact">
                {{ t("footer.contact") }}
              </RouterLink>
            </li>
            <li>
              <RouterLink class="text-canvas/75 transition-colors hover:text-amber-ink" to="/feedback/new">
                {{ t("footer.feedback") }}
              </RouterLink>
            </li>
          </ul>

          <div v-if="safeSocial.length" class="mt-6">
            <h2 class="eyebrow text-canvas/50">{{ t("footer.social") }}</h2>
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
            {{ t("footer.reduceMotion") }}
          </label>
          <button
            type="button"
            class="link-underline"
            @click="preferences.setTheme(preferences.theme === 'dark' ? 'light' : 'dark')"
          >
            {{ preferences.theme === "dark" ? t("footer.themeToLight") : t("footer.themeToDark") }}
          </button>
        </div>
      </div>
      <p v-if="site.error" class="mt-4 text-xs text-wine-soft" role="status">
        {{ t("footer.configFallback") }}
      </p>
    </div>
  </footer>
</template>
