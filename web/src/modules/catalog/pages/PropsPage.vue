<script setup lang="ts">
import { computed } from "vue";

import CategoryShowcase from "@/modules/catalog/components/CategoryShowcase.vue";
import { useI18n } from "@/shared/lib/i18n";
import { AppButton } from "@/shared/ui/base";

const { t } = useI18n();
const faqs = computed(() => [
  { q: t("propsPage.faq1Q"), a: t("propsPage.faq1A") },
  { q: t("propsPage.faq2Q"), a: t("propsPage.faq2A") },
  { q: t("propsPage.faq3Q"), a: t("propsPage.faq3A") },
]);
</script>

<template>
  <CategoryShowcase
    category="props"
    :eyebrow="t('propsPage.eyebrow')"
    :title="t('propsPage.title')"
    :intro="t('propsPage.intro')"
    :grid-label="t('propsPage.grid')"
    :grid-note="t('propsPage.gridNote')"
    :empty-label="t('propsPage.empty')"
    tone="grape"
    :faqs="faqs"
  >
    <section class="container-page py-12">
      <div class="relative overflow-hidden rounded-[2rem] border-2 border-ink/10 bg-grape-soft px-6 py-10 sm:px-10">
        <div class="pointer-events-none absolute -right-10 -top-10 size-48 rounded-full bg-grape/15 blur-2xl" aria-hidden="true" />
        <div class="relative flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
          <div>
            <p class="text-xs font-extrabold uppercase tracking-[0.2em] text-grape">CUSTOM</p>
            <h2 class="mt-2 font-display text-3xl font-extrabold">{{ t("propsPage.customTitle") }}</h2>
            <p class="mt-2 max-w-xl text-sm leading-relaxed text-ink-soft">{{ t("propsPage.customBody") }}</p>
          </div>
          <AppButton to="/contact" variant="primary" size="lg">{{ t("propsPage.customCta") }}</AppButton>
        </div>
      </div>
    </section>
  </CategoryShowcase>
</template>
