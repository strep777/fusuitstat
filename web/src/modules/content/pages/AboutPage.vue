<script setup lang="ts">
/**
 * 关于工作室（FR-001）。
 *
 * 信息顺序：工作室介绍（含工作方式与制作环境）→ 团队成员 → 合作成员 → 经营主体与联系信息。
 * 约束：人名与照片只来自 `content/team`；没有公开展示授权的条目只显示文字，不显示占位头像；
 * 介绍正文来自 `content/page/about`，没有发布时显示诚实的空态，不编造资历、客户或竞品数据。
 */
import { computed, onMounted, ref } from "vue";

import {
  applyPageHead,
  classifyPublicError,
  fetchContentDetail,
  fetchContentList,
  type ContentDetail,
  type ContentSummary,
} from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { truncate } from "@/shared/lib/format";
import {
  AppButton,
  Breadcrumbs,
  EmptyState,
  SectionHeading,
  SkeletonBlock,
  StatusBadge,
  SurfaceCard,
} from "@/shared/ui/base";

const site = useSiteStore();

const about = ref<ContentDetail | null>(null);
const aboutLoading = ref(true);
const aboutError = ref<string | null>(null);
const aboutMissing = ref(false);
const aboutBlocked = ref(false);

const team = ref<ContentSummary[]>([]);
const teamLoading = ref(true);
const teamError = ref<string | null>(null);
const teamBlocked = ref(false);

/** 合作成员与团队成员共用 team 条目，用标签区分；没有标签就不猜。 */
const PARTNER_TAG = /合作|伙伴|外包|协作|联名|顾问|partner/i;

const members = computed(() => team.value.filter((item) => !item.tags.some((tag) => PARTNER_TAG.test(tag))));
const partners = computed(() => team.value.filter((item) => item.tags.some((tag) => PARTNER_TAG.test(tag))));

const breadcrumbs = [
  { label: "首页", to: "/" },
  { label: "关于工作室" },
];

const workflow = computed(() => [
  { label: "第一步", value: "提交询价", note: "说明角色、系列意向与预算区间，可附设定图。" },
  {
    label: "第二步",
    value: `报价有效期 ${site.commissionPolicy.quote_valid_days} 天`,
    note: "报价是带期限的提案，包含项与不含项分开列出。",
  },
  {
    label: "第三步",
    value: `预付款 ${site.commissionPolicy.prepayment_percent}%`,
    note: `签合同后进入排期，尾款在 ${site.commissionPolicy.balance_due_days} 天内结清。`,
  },
  {
    label: "第四步",
    value: `免费修改 ${site.commissionPolicy.free_revision_count} 次`,
    note: "阶段确认留痕，超出次数的修改按变更单另行报价。",
  },
]);

async function loadAbout(): Promise<void> {
  aboutLoading.value = true;
  aboutError.value = null;
  aboutMissing.value = false;
  aboutBlocked.value = false;
  try {
    const entry = await fetchContentDetail("page", "about");
    about.value = entry;
    applyPageHead({
      title: entry.seo.title || "关于工作室",
      description: entry.seo.description || "关于工作室的团队、工作方式与制作环境。",
      canonical: "/about",
      ogType: "website",
      ogImage: entry.cover?.src,
    });
  } catch (caught) {
    const state = classifyPublicError(caught, "工作室介绍加载失败。");
    if (state.kind === "not-found") aboutMissing.value = true;
    else if (state.kind === "forbidden" || state.kind === "login") aboutBlocked.value = true;
    else aboutError.value = state.message;
  } finally {
    aboutLoading.value = false;
  }
}

async function loadTeam(): Promise<void> {
  teamLoading.value = true;
  teamError.value = null;
  teamBlocked.value = false;
  try {
    const page = await fetchContentList("team", { limit: 60, page: 1 });
    team.value = page.items;
  } catch (caught) {
    const state = classifyPublicError(caught, "团队信息加载失败。");
    if (state.kind === "forbidden" || state.kind === "login") teamBlocked.value = true;
    else teamError.value = state.message;
  } finally {
    teamLoading.value = false;
  }
}

onMounted(() => {
  applyPageHead({
    title: "关于工作室",
    description:
      "弈星纪元兽装工作室的团队、工作方式、制作环境与合作成员；人名与照片只展示已取得授权的内容条目。",
    canonical: "/about",
    ogType: "website",
  });
  void Promise.allSettled([site.load(), loadAbout(), loadTeam()]);
});
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-10 pt-6">
      <SectionHeading
        eyebrow="About"
        level="1"
        title="关于弈星纪元兽装工作室"
        description="这一页说明工作室由谁组成、按什么方式协作、在什么条件下制作。人名、照片与分工都来自运营已发布的团队条目，没有授权的信息不会出现。"
      />
    </section>

    <!-- 工作室介绍：正文来自 content/page/about -->
    <section class="container-page pb-14">
      <SurfaceCard as="div">
        <div v-if="aboutLoading" class="space-y-4">
          <p class="eyebrow">工作室介绍</p>
          <SkeletonBlock :lines="6" aria-label="正在加载工作室介绍" />
        </div>

        <template v-else-if="about">
          <p class="eyebrow">工作室介绍</p>
          <div
            v-if="about.body_html.trim()"
            class="prose-quiet mt-4 max-w-[720px] text-base leading-relaxed text-ink-soft"
            v-html="about.body_html"
          />
          <p v-else class="mt-4 text-sm text-ink-muted">
            条目已创建，但正文还没有写完。运营完善并预览后会显示在这里，我们不用临时文案占位。
          </p>
          <p v-if="about.updated_at" class="mt-6 text-xs text-ink-faint">
            最近更新：{{ about.updated_at.slice(0, 10) }}
          </p>
        </template>

        <EmptyState
          v-else-if="aboutMissing"
          tone="not-found"
          title="工作室介绍尚未发布"
          description="介绍正文、工作方式与制作环境由运营在后台发布；没有核准的内容不会用占位文字替代。你仍然可以通过客服邮箱了解具体情况。"
        >
          <AppButton size="sm" variant="secondary" to="/contact">联系工作室</AppButton>
        </EmptyState>

        <EmptyState
          v-else-if="aboutBlocked"
          tone="forbidden"
          title="工作室介绍当前不可见"
          description="这份内容需要相应权限才能查看；如果你是访客，可以稍后重试或直接联系客服。"
        >
          <AppButton size="sm" to="/login">登录后重试</AppButton>
        </EmptyState>

        <EmptyState
          v-else
          tone="error"
          :title="aboutError ?? '工作室介绍加载失败'"
          description="网络恢复后可以重试；已发布的其他区块不受影响。"
        >
          <AppButton size="sm" @click="loadAbout()">重新加载</AppButton>
        </EmptyState>
      </SurfaceCard>
    </section>

    <!-- 团队成员 -->
    <section class="border-y border-line bg-surface-muted">
      <div class="container-page py-14">
        <SectionHeading
          eyebrow="Team"
          title="团队成员"
          description="照片只在素材已授权公开时展示；没有授权照片的成员只显示文字介绍。"
        />

        <div v-if="teamLoading" class="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <SurfaceCard v-for="index in 3" :key="index" class="h-full">
            <SkeletonBlock :lines="3" aria-label="正在加载团队成员" />
          </SurfaceCard>
        </div>

        <div v-else-if="team.length" class="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <SurfaceCard v-for="member in members" :key="member.id" class="flex h-full flex-col">
            <div v-if="member.cover" class="mb-4 overflow-hidden rounded-md bg-canvas-sunk">
              <img
                :src="member.cover.thumb ?? member.cover.src"
                :srcset="member.cover.srcset.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
                sizes="(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw"
                :width="member.cover.width"
                :height="member.cover.height"
                :alt="member.cover.alt || `${member.title} 的工作照`"
                loading="lazy"
                decoding="async"
                class="aspect-[4/3] w-full object-cover"
              />
            </div>
            <h3 class="font-display text-lg">{{ member.title }}</h3>
            <div v-if="member.tags.length" class="mt-2 flex flex-wrap gap-1.5">
              <StatusBadge v-for="tag in member.tags" :key="tag" tone="neutral" :label="tag" />
            </div>
            <p v-if="member.summary" class="mt-3 text-sm leading-relaxed text-ink-muted">
              {{ truncate(member.summary, 140) }}
            </p>
            <p v-else class="mt-3 text-sm text-ink-faint">这位成员的分工说明还在整理中。</p>
          </SurfaceCard>
        </div>

        <EmptyState
          v-else-if="teamBlocked"
          class="mt-8"
          tone="forbidden"
          title="团队信息当前不可见"
          description="团队条目需要权限才能读取。访客可以稍后再试，或通过客服邮箱询问对接人。"
        />

        <EmptyState
          v-else-if="teamError"
          class="mt-8"
          tone="error"
          :title="teamError"
          description="团队条目加载失败，重试不会影响其他区块。"
        >
          <AppButton size="sm" @click="loadTeam()">重新加载团队信息</AppButton>
        </EmptyState>

        <EmptyState
          v-else
          class="mt-8"
          title="团队信息尚未发布"
          description="成员姓名与分工由运营在后台录入并授权后发布；没有数据时不会用化名或网图填充。"
        />
      </div>
    </section>

    <!-- 合作成员 -->
    <section class="container-page py-14">
      <SectionHeading
        eyebrow="Collaborators"
        title="合作成员"
        description="外部合作方按项目参与，署名以标签区分；合作范围与授权在条目内说明。"
      />

      <div v-if="teamLoading" class="mt-8">
        <SkeletonBlock :lines="3" aria-label="正在加载合作成员" />
      </div>

      <div v-else-if="partners.length" class="mt-8 grid gap-4 sm:grid-cols-2">
        <SurfaceCard v-for="partner in partners" :key="partner.id">
          <div class="flex items-start justify-between gap-4">
            <h3 class="font-display text-base">{{ partner.title }}</h3>
            <div class="flex shrink-0 flex-wrap justify-end gap-1.5">
              <StatusBadge v-for="tag in partner.tags.slice(0, 3)" :key="tag" tone="amber" :label="tag" />
            </div>
          </div>
          <p v-if="partner.summary" class="mt-3 text-sm text-ink-muted">{{ truncate(partner.summary, 120) }}</p>
        </SurfaceCard>
      </div>

      <EmptyState
        v-else
        class="mt-8"
        title="合作成员尚未发布"
        description="合作方署名需要本人授权后才会公开；没有授权时这一区块保持为空。"
      />
    </section>

    <!-- 工作方式：数值来自后台配置，不用估算值 -->
    <section class="border-y border-line bg-surface-muted">
      <div class="container-page py-14">
        <SectionHeading
          eyebrow="How we work"
          title="工作方式"
          description="下列数值由后台统一配置，页面展示与服务端校验使用同一份数据；没有核准的交期不会写在这里。"
        >
          <template #actions>
            <AppButton to="/commission/guide" size="sm" variant="secondary">阅读委托指南</AppButton>
          </template>
        </SectionHeading>

        <dl class="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          <SurfaceCard v-for="step in workflow" :key="step.label" as="div">
            <dt class="eyebrow">{{ step.label }}</dt>
            <dd class="mt-2 font-display text-lg">{{ step.value }}</dd>
            <dd class="mt-2 text-sm leading-relaxed text-ink-muted">{{ step.note }}</dd>
          </SurfaceCard>
        </dl>
      </div>
    </section>

    <!-- 经营主体与联系信息 -->
    <section class="container-page py-14">
      <SectionHeading eyebrow="Legal entity" title="经营主体与联系信息" />

      <div class="mt-8 grid gap-6 lg:grid-cols-[1.2fr_1fr]">
        <SurfaceCard as="div">
          <dl class="grid gap-5 text-sm sm:grid-cols-2">
            <div>
              <dt class="text-ink-faint">品牌</dt>
              <dd class="mt-1 font-medium">{{ site.brand.brand_name }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">经营主体</dt>
              <dd class="mt-1">{{ site.brand.legal_entity }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">站点域名</dt>
              <dd class="mt-1">{{ site.brand.domain }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">客服邮箱</dt>
              <dd class="mt-1">
                <a class="link-underline hover:text-amber-strong" :href="`mailto:${site.brand.support_email}`">
                  {{ site.brand.support_email }}
                </a>
              </dd>
            </div>
            <div class="sm:col-span-2">
              <dt class="text-ink-faint">工作时段</dt>
              <dd class="mt-1">{{ site.brand.service_hours }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">询价首响</dt>
              <dd class="mt-1">{{ site.brand.response_sla.inquiry_workdays }} 个工作日</dd>
            </div>
            <div>
              <dt class="text-ink-faint">反馈首响</dt>
              <dd class="mt-1">{{ site.brand.response_sla.feedback_workdays }} 个工作日</dd>
            </div>
          </dl>
        </SurfaceCard>

        <SurfaceCard class="!bg-[#f1e4d7]" as="div">
          <p class="eyebrow">Next</p>
          <h3 class="mt-3 font-display text-2xl font-medium">想开始一个角色？</h3>
          <p class="mt-3 text-sm leading-relaxed text-ink-muted">
            先读委托指南了解可接范围与售后，再提交询价。也可以直接发邮件说明需求，两条路径都会有人处理。
          </p>
          <div class="mt-6 flex flex-wrap gap-3">
            <AppButton to="/commission/new" size="sm">提交询价</AppButton>
            <AppButton to="/contact" variant="secondary" size="sm">联系与反馈</AppButton>
          </div>
        </SurfaceCard>
      </div>
    </section>
  </div>
</template>

<style scoped>
.prose-quiet :deep(h2) {
  margin-top: 1.5rem;
  margin-bottom: 0.5rem;
  font-size: 1.125rem;
  font-weight: 600;
}
.prose-quiet :deep(h3) {
  margin-top: 1.25rem;
  margin-bottom: 0.5rem;
  font-weight: 600;
}
.prose-quiet :deep(p) {
  margin-bottom: 0.75rem;
}
.prose-quiet :deep(ul) {
  list-style: disc;
  margin-bottom: 0.75rem;
  padding-left: 1.25rem;
}
.prose-quiet :deep(a) {
  text-decoration: underline;
  text-underline-offset: 3px;
}
</style>
