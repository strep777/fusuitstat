<script setup lang="ts">
import { computed } from "vue";

import CategoryShowcase from "@/modules/catalog/components/CategoryShowcase.vue";
import { useI18n } from "@/shared/lib/i18n";
import { AppButton } from "@/shared/ui/base";

const { t } = useI18n();
const faqs = computed(() => [
  { q: t("merchPage.faq1Q"), a: t("merchPage.faq1A") },
  { q: t("merchPage.faq2Q"), a: t("merchPage.faq2A") },
  { q: t("merchPage.faq3Q"), a: t("merchPage.faq3A") },
]);
</script>

<template>
  <CategoryShowcase
    category="merch"
    :eyebrow="t('merchPage.eyebrow')"
    :title="t('merchPage.title')"
    :intro="t('merchPage.intro')"
    :grid-label="t('merchPage.grid')"
    :grid-note="t('merchPage.gridNote')"
    :empty-label="t('merchPage.empty')"
    tone="lemon"
    :faqs="faqs"
  >
    <section class="container-page py-12">
      <div class="relative overflow-hidden rounded-[2rem] bg-[#f8eedf] px-6 py-10 shadow-soft sm:px-10">
        <div class="pointer-events-none absolute -bottom-12 -right-6 text-[10rem] leading-none opacity-10" aria-hidden="true">✦</div>
        <div class="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div>
            <p class="text-[11px] uppercase tracking-[0.18em] text-ink-muted">BUNDLE</p>
            <h2 class="mt-2 font-display text-3xl font-medium">{{ t("merchPage.bundleTitle") }}</h2>
            <p class="mt-2 max-w-xl text-sm leading-relaxed text-ink-soft">{{ t("merchPage.bundleBody") }}</p>
          </div>
          <AppButton to="/contact" variant="primary" size="lg">{{ t("merchPage.bundleCta") }}</AppButton>
        </div>
      </div>
    </section>
  </CategoryShowcase>
</template>
