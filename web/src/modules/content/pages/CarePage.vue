<script setup lang="ts">
/**
 * 养护与清洁（FR-024 / 03 文档 /care）。
 *
 * 内容全部取自 `content/page/care` 的正文（服务端已清洗）。穿戴、清洁、运输、存放四类
 * 指南由运营在正文里用二级标题组织，这里解析出章节做页内导航。
 * 材料说明必须与实际一致：页面不自行补充材料参数，也不承诺未核准的清洗方式；
 * 正文没有发布时显示诚实的空态。
 */
import { computed, onMounted, ref } from "vue";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentDetail,
  type ContentDetail,
} from "@/modules/content/api/content";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, SurfaceCard } from "@/shared/ui/base";

const entry = ref<ContentDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const missing = ref(false);
const blocked = ref(false);

const breadcrumbs = [
  { label: "首页", to: "/" },
  { label: "养护与清洁" },
];

interface Outline {
  id: string;
  label: string;
}

/**
 * 解析正文里的二级标题做页内目录。
 * 只读取标题文本并注入锚点 id，不重写正文内容；解析失败时退回原始 HTML。
 */
const parsed = computed<{ html: string; sections: Outline[] }>(() => {
  const html = entry.value?.body_html ?? "";
  if (!html || typeof DOMParser === "undefined") return { html, sections: [] };
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const sections: Outline[] = [];
    doc.querySelectorAll("h2").forEach((heading, position) => {
      const label = (heading.textContent ?? "").trim();
      if (!label) return;
      const id = `care-section-${position + 1}`;
      heading.setAttribute("id", id);
      sections.push({ id, label });
    });
    return { html: doc.body.innerHTML, sections };
  } catch {
    return { html, sections: [] };
  }
});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  missing.value = false;
  blocked.value = false;
  try {
    const detail = await fetchContentDetail("page", "care");
    entry.value = detail;
    applyPageHead({
      title: detail.seo.title || "养护与清洁",
      description: detail.seo.description || "兽装的穿戴、清洁、运输与存放指南。",
      canonical: "/care",
      ogType: "article",
      ogImage: detail.cover?.src,
    });
  } catch (caught) {
    const state = classifyPublicError(caught, "养护指南加载失败。");
    if (state.kind === "not-found") missing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") blocked.value = true;
    else error.value = state.message;
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  applyPageHead({
    title: "养护与清洁",
    description: "兽装的穿戴、清洁、运输与存放指南，材料说明与交付随附说明保持一致。",
    canonical: "/care",
    ogType: "website",
  });
  void load();
});
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-8 pt-6">
      <SectionHeading
        eyebrow="Care"
        level="1"
        title="养护与清洁"
        description="按顺序做：穿戴前检查、按材料清洁、正确运输、干燥后存放。指南由运营按实际使用的材料维护，页面不代替交付时随附的说明。"
      />
    </section>

    <div class="container-page pb-16">
      <div v-if="loading" class="space-y-4">
        <SkeletonBlock :lines="5" aria-label="正在加载养护指南" />
        <SkeletonBlock :lines="5" aria-label="正在加载养护指南" />
      </div>

      <EmptyState
        v-else-if="blocked"
        tone="forbidden"
        title="养护指南当前不可见"
        description="这份内容需要权限才能查看。可以稍后重试，或直接联系客服索取养护说明。"
      >
        <AppButton size="sm" to="/contact">联系客服</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="missing"
        tone="not-found"
        title="养护指南尚未发布"
        description="穿戴、清洁、运输与存放说明由运营在后台发布；内容未核准前不会用通用模板替代。收到成品时请先按随附说明操作，也可以发邮件索取。"
      >
        <AppButton size="sm" variant="secondary" to="/contact">索取养护说明</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="error"
        tone="error"
        :title="error"
        description="网络恢复后可以重试；重试只重新读取这一份指南。"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <div v-else-if="entry" class="grid gap-10 lg:grid-cols-[220px_1fr]">
        <!-- 页内导航：章节来自正文的二级标题 -->
        <nav v-if="parsed.sections.length" class="lg:sticky lg:top-24 lg:self-start" aria-label="养护指南目录">
          <p class="eyebrow">目录</p>
          <ol class="mt-3 space-y-2 text-sm">
            <li v-for="section in parsed.sections" :key="section.id">
              <a class="link-underline text-ink-muted hover:text-amber-strong" :href="`#${section.id}`">
                {{ section.label }}
              </a>
            </li>
          </ol>
        </nav>
        <p v-else class="text-sm text-ink-faint lg:col-start-1">正文还没有分节标题。</p>

        <article class="min-w-0">
          <div
            v-if="entry.cover"
            class="mb-8 overflow-hidden rounded-lg border border-line bg-canvas-sunk"
          >
            <img
              :src="entry.cover.src"
              :srcset="entry.cover.srcset.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
              sizes="(min-width: 1024px) 900px, 100vw"
              :width="entry.cover.width"
              :height="entry.cover.height"
              :alt="entry.cover.alt || `${entry.title} 配图`"
              loading="lazy"
              decoding="async"
              class="w-full object-cover"
            />
          </div>

          <div class="prose-care max-w-[720px] text-base leading-relaxed text-ink-soft" v-html="parsed.html" />

          <p v-if="entry.updated_at" class="mt-8 text-xs text-ink-faint">
            最近更新：{{ entry.updated_at.slice(0, 10) }}
          </p>

          <SurfaceCard class="mt-8" as="div">
            <h2 class="font-display text-lg">材料与清洗方式以交付说明为准</h2>
            <p class="mt-3 text-sm leading-relaxed text-ink-muted">
              不同批次的毛绒、内衬与支撑材料可能不同，能用的清洁剂和干燥温度也不同。清洗前先看随成品交付的说明；
              说明与本页内容不一致时，以交付说明为准，并可以联系我们确认。
            </p>
            <div class="mt-5 flex flex-wrap gap-3">
              <AppButton to="/faq" size="sm" variant="secondary">常见问题</AppButton>
              <AppButton to="/legal/returns" size="sm" variant="secondary">维修与退换规则</AppButton>
              <AppButton to="/contact" size="sm" variant="secondary">联系客服</AppButton>
            </div>
          </SurfaceCard>
        </article>
      </div>
    </div>
  </div>
</template>

<style scoped>
.prose-care :deep(h2) {
  margin-top: 2rem;
  margin-bottom: 0.75rem;
  font-family: var(--font-display);
  font-size: 1.375rem;
  font-weight: 600;
}
.prose-care :deep(h2:first-child) {
  margin-top: 0;
}
.prose-care :deep(h3) {
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.0625rem;
  font-weight: 600;
}
.prose-care :deep(p) {
  margin-bottom: 0.875rem;
}
.prose-care :deep(ul),
.prose-care :deep(ol) {
  margin-bottom: 0.875rem;
  padding-left: 1.25rem;
}
.prose-care :deep(ul) {
  list-style: disc;
}
.prose-care :deep(ol) {
  list-style: decimal;
}
.prose-care :deep(li) {
  margin-bottom: 0.375rem;
}
.prose-care :deep(a) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.prose-care :deep(img) {
  margin: 1rem 0;
  border-radius: var(--radius-md);
}
.prose-care :deep(table) {
  width: 100%;
  margin: 1rem 0;
  border-collapse: collapse;
  font-size: 0.9375rem;
}
.prose-care :deep(th),
.prose-care :deep(td) {
  border: 1px solid var(--color-line);
  padding: 0.5rem 0.75rem;
  text-align: left;
}
</style>
