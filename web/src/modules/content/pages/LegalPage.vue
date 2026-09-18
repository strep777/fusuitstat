<script setup lang="ts">
/**
 * 规则与条款（FR-018 / 03 文档 /legal/:slug）。
 *
 * slug ∈ terms / privacy / returns / points，非法 slug 直接给 not-found 语义，
 * 不重定向、不猜内容。页面顶部显示版本号与生效时间——都从条目数据读取，
 * 条目里没有登记时如实显示“待运营发布”，不套用其它文档的版本号。
 * 行使隐私权利与投诉的公开路径不强制短信登录：这里给出客服邮箱与规则入口。
 */
import { computed, onMounted, ref, watch } from "vue";
import { RouterLink, useRoute } from "vue-router";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentDetail,
  type ContentDetail,
} from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { formatDate } from "@/shared/lib/format";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, SurfaceCard } from "@/shared/ui/base";

const route = useRoute();
const site = useSiteStore();

const LEGAL_DOCS = [
  { slug: "terms", label: "用户服务协议", hint: "服务范围、下单与付款、责任划分" },
  { slug: "privacy", label: "隐私政策", hint: "收集范围、短信与支付服务商、数据权利" },
  { slug: "returns", label: "取消与退换规则", hint: "定制件与现货件分开处理" },
  { slug: "points", label: "星绒币规则", hint: "获取、调整、有效期与申诉" },
] as const;

const entry = ref<ContentDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const missing = ref(false);
const blocked = ref(false);
const invalidSlug = ref(false);

const slug = computed(() => {
  const value = route.params.slug;
  const raw = Array.isArray(value) ? value[0] : value;
  return (raw ?? "").toLowerCase();
});

const activeDoc = computed(() => LEGAL_DOCS.find((doc) => doc.slug === slug.value) ?? null);

const PLACEHOLDER = "待运营发布";

/** 从标签里读取版本号（运营登记写法如 `version:1.2` / `版本：1.2`）；没有登记就返回 null。 */
const version = computed<string | null>(() => {
  for (const tag of entry.value?.tags ?? []) {
    const parts = tag.split(/[:=：]/);
    const key = parts[0]?.trim().toLowerCase();
    const value = parts.slice(1).join(":").trim();
    if (value && (key === "version" || key === "版本" || key === "v")) return value;
  }
  return null;
});

/** 生效时间取发布时间，缺省回落到最近更新时间。 */
const effectiveAt = computed<string | null>(() => {
  const raw = entry.value?.published_at ?? entry.value?.updated_at ?? null;
  if (!raw) return null;
  const formatted = formatDate(raw);
  return formatted === "—" ? null : formatted;
});

interface Outline {
  id: string;
  label: string;
}

const parsed = computed<{ html: string; sections: Outline[] }>(() => {
  const html = entry.value?.body_html ?? "";
  if (!html || typeof DOMParser === "undefined") return { html, sections: [] };
  try {
    const doc = new DOMParser().parseFromString(html, "text/html");
    const sections: Outline[] = [];
    doc.querySelectorAll("h2").forEach((heading, position) => {
      const label = (heading.textContent ?? "").trim();
      if (!label) return;
      const id = `legal-section-${position + 1}`;
      heading.setAttribute("id", id);
      sections.push({ id, label });
    });
    return { html: doc.body.innerHTML, sections };
  } catch {
    return { html, sections: [] };
  }
});

const breadcrumbs = computed(() => [
  { label: "首页", to: "/" },
  { label: "规则与条款", to: "/legal/terms" },
  { label: entry.value?.title ?? activeDoc.value?.label ?? "条款" },
]);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  missing.value = false;
  blocked.value = false;
  invalidSlug.value = false;

  if (!activeDoc.value) {
    invalidSlug.value = true;
    loading.value = false;
    entry.value = null;
    applyPageHead({
      title: "规则与条款",
      description: "本站的规则与条款入口：用户服务协议、隐私政策、取消与退换规则与星绒币规则。",
      canonical: "/legal/terms",
      robots: "noindex, follow",
    });
    return;
  }

  try {
    const detail = await fetchContentDetail("legal", activeDoc.value.slug);
    entry.value = detail;
    applyPageHead({
      title: detail.seo.title || activeDoc.value.label,
      description: detail.seo.description || `${activeDoc.value.label}：${activeDoc.value.hint}。`,
      canonical: `/legal/${activeDoc.value.slug}`,
      ogType: "article",
    });
  } catch (caught) {
    entry.value = null;
    const state = classifyPublicError(caught, "这份条款加载失败。");
    if (state.kind === "not-found") missing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") blocked.value = true;
    else error.value = state.message;
    applyPageHead({
      title: activeDoc.value.label,
      description: `${activeDoc.value.label}：${activeDoc.value.hint}。`,
      robots: "noindex, follow",
    });
  } finally {
    loading.value = false;
  }
}

onMounted(load);
watch(slug, load);
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-6 pt-6">
      <SectionHeading
        eyebrow="Legal"
        level="1"
        :title="entry?.title ?? activeDoc?.label ?? '规则与条款'"
        :description="
          entry?.summary ||
          activeDoc?.hint ||
          '规则文本由运营在后台维护并发布；这里只会显示已经发布的版本。'
        "
      />
    </section>

    <!-- 版本与生效时间 -->
    <section class="container-page pb-10">
      <SurfaceCard as="div">
        <dl class="grid gap-6 text-sm sm:grid-cols-3">
          <div>
            <dt class="eyebrow">文档</dt>
            <dd class="mt-2 text-ink-soft">{{ activeDoc?.label ?? "未匹配到规则文档" }}</dd>
          </div>
          <div>
            <dt class="eyebrow">版本号</dt>
            <dd class="mt-2 text-ink-soft tabular-nums">{{ version ?? PLACEHOLDER }}</dd>
          </div>
          <div>
            <dt class="eyebrow">生效时间</dt>
            <dd class="mt-2 text-ink-soft tabular-nums">{{ effectiveAt ?? PLACEHOLDER }}</dd>
          </div>
        </dl>
        <p class="mt-5 text-xs text-ink-faint">
          版本号与生效时间登记在条目数据里；显示“{{ PLACEHOLDER }}”表示运营还没有登记，不代表条款没有版本。
          条款变更会更新版本号，历史版本保留在后台。
        </p>
      </SurfaceCard>
    </section>

    <div class="container-page grid gap-10 pb-16 lg:grid-cols-[1fr_260px]">
      <div class="min-w-0">
        <div v-if="loading" class="space-y-4">
          <SkeletonBlock :lines="5" aria-label="正在加载条款正文" />
          <SkeletonBlock :lines="5" aria-label="正在加载条款正文" />
        </div>

        <!-- 非法 slug：真实 not-found 语义 -->
        <EmptyState
          v-else-if="invalidSlug"
          tone="not-found"
          title="没有这份规则文档"
          description="本站的规则文档只有下面四份。地址可能拼写有误，也可能是旧链接。"
        >
          <AppButton size="sm" to="/legal/terms">用户服务协议</AppButton>
          <AppButton size="sm" variant="secondary" to="/legal/privacy">隐私政策</AppButton>
          <AppButton size="sm" variant="secondary" to="/">返回首页</AppButton>
        </EmptyState>

        <EmptyState
          v-else-if="missing"
          tone="not-found"
          title="这份条款尚未发布"
          description="规则文本由运营在后台维护并预览后发布；在发布之前这里不会显示草稿内容。需要了解具体规则时可以先联系客服。"
        >
          <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
        </EmptyState>

        <EmptyState
          v-else-if="blocked"
          tone="forbidden"
          title="这份条款当前不可见"
          description="内容需要权限才能读取。访客可以稍后重试，或通过客服邮箱索取相关规则说明。"
        >
          <AppButton size="sm" to="/contact">联系客服</AppButton>
        </EmptyState>

        <EmptyState
          v-else-if="error"
          tone="error"
          :title="error"
          description="网络恢复后可以重试；也可以先看其它规则文档。"
        >
          <AppButton size="sm" @click="load()">重新加载</AppButton>
        </EmptyState>

        <template v-else-if="entry">
          <nav v-if="parsed.sections.length" class="mb-8 rounded-lg border border-line bg-surface-muted p-5" aria-label="条款目录">
            <p class="eyebrow">目录</p>
            <ol class="mt-3 grid gap-2 text-sm sm:grid-cols-2">
              <li v-for="section in parsed.sections" :key="section.id">
                <a class="link-underline text-ink-muted hover:text-amber-strong" :href="`#${section.id}`">
                  {{ section.label }}
                </a>
              </li>
            </ol>
          </nav>

          <article class="prose-legal max-w-[720px] text-base leading-relaxed text-ink-soft" v-html="parsed.html" />

          <p class="mt-8 text-xs text-ink-faint">
            文档版本：版本号 {{ version ?? PLACEHOLDER }} · 生效时间 {{ effectiveAt ?? PLACEHOLDER }} ·
            经营主体 {{ site.brand.legal_entity }}
          </p>
        </template>
      </div>

      <!-- 侧栏：四份文档入口与联系路径 -->
      <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
        <SurfaceCard as="div">
          <h2 class="font-display text-base">全部规则文档</h2>
          <ul class="mt-4 space-y-3 text-sm">
            <li v-for="doc in LEGAL_DOCS" :key="doc.slug">
              <RouterLink
                class="block rounded-sm px-3 py-2 transition-colors"
                :class="
                  doc.slug === slug
                    ? 'bg-amber-soft text-amber-strong'
                    : 'text-ink-soft hover:bg-surface-muted hover:text-ink'
                "
                :to="`/legal/${doc.slug}`"
                :aria-current="doc.slug === slug ? 'page' : undefined"
              >
                <span class="font-medium">{{ doc.label }}</span>
                <span class="mt-1 block text-xs text-ink-faint">{{ doc.hint }}</span>
              </RouterLink>
            </li>
          </ul>
        </SurfaceCard>

        <SurfaceCard as="div">
          <h2 class="font-display text-base">权利请求与投诉</h2>
          <p class="mt-3 text-sm text-ink-muted">
            不需要短信登录：直接发邮件到
            <a class="link-underline hover:text-amber-strong" :href="`mailto:${site.brand.support_email}`">
              {{ site.brand.support_email }}
            </a>
            就可以提出查阅、更正、删除等请求或投诉。
          </p>
          <dl class="mt-4 grid gap-3 text-sm">
            <div>
              <dt class="text-ink-faint">经营主体</dt>
              <dd class="mt-1">{{ site.brand.legal_entity }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">工作时段</dt>
              <dd class="mt-1">{{ site.brand.service_hours }}</dd>
            </div>
          </dl>
          <div class="mt-5 flex flex-wrap gap-3">
            <AppButton size="sm" variant="secondary" to="/contact">联系与反馈</AppButton>
          </div>
        </SurfaceCard>
      </aside>
    </div>
  </div>
</template>

<style scoped>
.prose-legal :deep(h2) {
  margin-top: 2rem;
  margin-bottom: 0.75rem;
  font-family: var(--font-display);
  font-size: 1.375rem;
  font-weight: 600;
}
.prose-legal :deep(h2:first-child) {
  margin-top: 0;
}
.prose-legal :deep(h3) {
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.0625rem;
  font-weight: 600;
}
.prose-legal :deep(p) {
  margin-bottom: 0.875rem;
}
.prose-legal :deep(ul),
.prose-legal :deep(ol) {
  margin-bottom: 0.875rem;
  padding-left: 1.25rem;
}
.prose-legal :deep(ul) {
  list-style: disc;
}
.prose-legal :deep(ol) {
  list-style: decimal;
}
.prose-legal :deep(li) {
  margin-bottom: 0.375rem;
}
.prose-legal :deep(a) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
.prose-legal :deep(table) {
  width: 100%;
  margin: 1rem 0;
  border-collapse: collapse;
  font-size: 0.9375rem;
}
.prose-legal :deep(th),
.prose-legal :deep(td) {
  border: 1px solid var(--color-line);
  padding: 0.5rem 0.75rem;
  text-align: left;
}
</style>
