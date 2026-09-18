<script setup lang="ts">
/**
 * 首页。
 *
 * 信息顺序（11 文档页面布局基线）：
 * 导航与接单状态 → 代表作品/品牌短句 → 系列 → 委托步骤 → 商品/角色 → 公告 → 联系与经营信息。
 *
 * 设计来源：森绒物语的温润质感 + 光海印记的信息层级 + 万物通行的规格对比 +
 * 爻光星的画廊网格 + 宇灵制造的系列叙事。首屏可见内容不等加载动画。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink } from "vue-router";

import { fetchHome, type HomeAggregate } from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { BrandMark, StarLoader } from "@/shared/ui/BrandMark";
import { AppButton, EmptyState, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatCountdown, formatMoney, formatPriceFrom, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const home = ref<HomeAggregate | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const site = useSiteStore();
const auth = useAuthStore();

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  try {
    home.value = await fetchHome();
    if (home.value.schedule) {
      site.setScheduleSummary(home.value.schedule);
    }
  } catch (caught) {
    error.value = caught instanceof ApiError ? caught.message : "首页内容加载失败。";
  } finally {
    loading.value = false;
  }
}

onMounted(load);

const highlights = computed(() => [
  { label: "手工定制", value: "全装 / 半装 / 单头" },
  { label: "交付方式", value: "阶段确认 + 出厂记录" },
  { label: "购买奖励", value: `${site.loyaltyRules.denominator_yuan} 元 = 1 星绒币` },
  { label: "人工响应", value: `${site.brand.response_sla.inquiry_workdays} 个工作日` },
]);

const scheduleBadge = computed(() => {
  const schedule = home.value?.schedule;
  if (!schedule) return { tone: "neutral" as const, label: "档期以公告为准" };
  if (schedule.status === "open") {
    return { tone: "jade" as const, label: `委托开放中 · 余量 ${schedule.remaining}` };
  }
  if (schedule.status === "upcoming") {
    return { tone: "amber" as const, label: `即将开放 · ${schedule.start_at.slice(0, 10)}` };
  }
  return { tone: "neutral" as const, label: "本轮申请已结束" };
});
</script>

<template>
  <div>
    <!-- 首屏：品牌短句与接单状态。深色面使用白色 Logo（素材在深底色下的对比度检查） -->
    <section class="border-b border-night-line bg-night text-canvas">
      <div class="container-page grid gap-12 py-16 lg:grid-cols-[1.1fr_0.9fr] lg:py-24">
        <div class="animate-fade-up">
          <div class="flex items-center gap-3">
            <BrandMark :size="56" variant="light" decorative />
            <div>
              <p class="eyebrow text-canvas/50">{{ home?.hero.brand_name ?? site.brand.brand_name }}</p>
              <p class="font-display text-lg">{{ home?.hero.tagline ?? "绒里有星，装配成纪" }}</p>
            </div>
          </div>

          <h1 class="mt-8 font-display text-4xl leading-[1.15] sm:text-5xl lg:text-6xl">
            把设定变成<br class="hidden sm:block" />可以穿在身上的角色
          </h1>
          <p class="mt-6 max-w-xl text-base leading-relaxed text-canvas/75">
            {{
              home?.hero.statement ??
              "从设定到实装，一步一步做成可以穿在身上的角色。工作室按季度开放委托名额，先询价、再报价，签合同后进入排期。"
            }}
          </p>

          <div class="mt-8 flex flex-wrap items-center gap-3">
            <AppButton :to="home?.hero.primary_action.to ?? '/commission/guide'" size="lg">
              {{ home?.hero.primary_action.label ?? "查看委托指南" }}
            </AppButton>
            <AppButton
              :to="home?.hero.secondary_action.to ?? '/works'"
              variant="secondary"
              size="lg"
              class="!border-night-line !bg-transparent !text-canvas hover:!border-amber-ink hover:!text-amber-ink"
            >
              {{ home?.hero.secondary_action.label ?? "浏览作品集" }}
            </AppButton>
            <RouterLink
              v-if="!auth.isAuthenticated"
              to="/login"
              class="link-underline text-sm text-canvas/70 hover:text-amber-ink"
            >
              手机号登录后提交询价
            </RouterLink>
          </div>

          <dl class="mt-12 grid grid-cols-2 gap-6 border-t border-night-line pt-8 sm:grid-cols-4">
            <div v-for="item in highlights" :key="item.label">
              <dt class="text-xs text-canvas/50">{{ item.label }}</dt>
              <dd class="mt-1 text-sm font-medium text-canvas/90">{{ item.value }}</dd>
            </div>
          </dl>
        </div>

        <!-- 深色面内的浅色信息卡：像爻光星那样把关键状态前置 -->
        <div class="flex flex-col gap-4">
          <SurfaceCard class="!bg-canvas !text-ink">
            <div class="flex items-center justify-between gap-3">
              <p class="eyebrow">当前档期</p>
              <StatusBadge :tone="scheduleBadge.tone" :label="scheduleBadge.label" />
            </div>
            <template v-if="home?.schedule">
              <h2 class="mt-4 font-display text-2xl">{{ home.schedule.title }}</h2>
              <dl class="mt-4 space-y-2 text-sm text-ink-muted">
                <div class="flex justify-between gap-4">
                  <dt>开始</dt>
                  <dd class="tabular-nums">{{ home.schedule.start_at.slice(0, 10) }}</dd>
                </div>
                <div class="flex justify-between gap-4">
                  <dt>截止</dt>
                  <dd class="tabular-nums">{{ home.schedule.end_at.slice(0, 10) }}</dd>
                </div>
                <div class="flex justify-between gap-4">
                  <dt>总名额 / 剩余</dt>
                  <dd class="tabular-nums">
                    {{ home.schedule.capacity }} / {{ home.schedule.remaining }}
                  </dd>
                </div>
                <div class="flex justify-between gap-4">
                  <dt>时区</dt>
                  <dd>{{ home.schedule.timezone }}</dd>
                </div>
              </dl>
              <p class="mt-4 text-xs text-ink-faint">
                名额、报价有效期与尾款期限都由后台配置；公开排期只展示匿名余量，不含个人信息。
              </p>
            </template>
            <template v-else>
              <p class="mt-4 text-sm text-ink-muted">
                当前没有正在开放的档期。开放时间以公告为准，你也可以先提交询价进入沟通。
              </p>
            </template>
            <div class="mt-5 flex flex-wrap gap-3">
              <AppButton to="/commission/schedules" size="sm" variant="secondary">查看全部档期</AppButton>
              <AppButton to="/commission/new" size="sm">提交询价</AppButton>
            </div>
          </SurfaceCard>

          <div class="grid grid-cols-2 gap-4">
            <RouterLink
              to="/fursuits"
              class="group rounded-lg border border-night-line p-5 transition-colors hover:border-amber-ink"
            >
              <p class="eyebrow text-canvas/50">系列规格</p>
              <p class="mt-2 font-display text-lg text-canvas">对比材质与交付内容</p>
              <p class="mt-2 text-xs text-canvas/60">包含项与不含项分开列出，起价不承诺未核准交期。</p>
            </RouterLink>
            <RouterLink
              to="/characters"
              class="group rounded-lg border border-night-line p-5 transition-colors hover:border-amber-ink"
            >
              <p class="eyebrow text-canvas/50">角色领养</p>
              <p class="mt-2 font-display text-lg text-canvas">独占角色只有一只</p>
              <p class="mt-2 text-xs text-canvas/60">标价、授权范围与领养状态在详情页说明。</p>
            </RouterLink>
          </div>
        </div>
      </div>
    </section>

    <!-- 加载与错误：首屏以下区块用局部状态，不遮罩整页 -->
    <div v-if="loading" class="container-page py-20">
      <StarLoader message="正在读取作品与系列" hint="首次加载可能需要一点时间" />
    </div>
    <div v-else-if="error" class="container-page py-16">
      <EmptyState
        tone="error"
        title="首页内容暂时无法加载"
        :description="`${error} 网络恢复后可以重试，已填写的内容不会丢失。`"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
        <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
      </EmptyState>
    </div>

    <template v-else-if="home">
      <!-- 代表作品：爻光星式画廊网格 -->
      <section class="container-page py-16 lg:py-20">
        <SectionHeading
          eyebrow="Selected works"
          title="代表作品"
          description="只展示已取得公开展示授权的成品；每件作品的物种、类型、装师与摄影署名都可核对。"
        >
          <template #actions>
            <AppButton :to="home.featured_works.more" variant="secondary" size="sm">
              查看全部 {{ home.featured_works.total }} 件
            </AppButton>
          </template>
        </SectionHeading>

        <div v-if="home.featured_works.items.length" class="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          <RouterLink
            v-for="work in home.featured_works.items"
            :key="work.id"
            :to="work.route"
            class="group block overflow-hidden rounded-lg border border-line bg-surface transition-shadow hover:shadow-lift"
          >
            <div class="aspect-[4/5] overflow-hidden bg-canvas-sunk">
              <img
                v-if="work.cover"
                :src="work.cover.thumb ?? work.cover.src"
                :srcset="work.cover.srcset.map((item) => `${item.url} ${item.width}w`).join(', ')"
                sizes="(min-width: 1024px) 33vw, (min-width: 640px) 50vw, 100vw"
                :width="work.cover.width"
                :height="work.cover.height"
                :alt="work.cover.alt || `${work.title} 成品展示`"
                loading="lazy"
                decoding="async"
                class="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
              />
              <div v-else class="flex size-full items-center justify-center text-xs text-ink-faint">
                图片准备中
              </div>
            </div>
            <div class="p-5">
              <div class="flex items-center gap-2">
                <StatusBadge v-if="work.species" tone="neutral" :label="work.species" />
                <StatusBadge v-if="work.work_type" tone="amber" :label="work.work_type" />
              </div>
              <h3 class="mt-3 font-display text-lg">{{ work.title }}</h3>
              <p class="mt-2 text-sm text-ink-muted">{{ truncate(work.summary, 72) }}</p>
            </div>
          </RouterLink>
        </div>
        <EmptyState
          v-else
          class="mt-10"
          title="作品正在整理中"
          description="我们不会用他人作品充当案例；已授权的成品整理完成后会在这里公开。"
        />
      </section>

      <!-- 系列：万物通行式规格对比 -->
      <section class="border-y border-line bg-surface-muted">
        <div class="container-page py-16 lg:py-20">
          <SectionHeading
            eyebrow="Series"
            title="系列与规格"
            description="不同系列在材质、结构与交付内容上差别明确。起价标注包含项与不含项，最终金额以工作人员发布的报价为准。"
          >
            <template #actions>
              <AppButton to="/fursuits" variant="secondary" size="sm">系列对比</AppButton>
            </template>
          </SectionHeading>

          <div v-if="home.series.length" class="mt-10 grid gap-6 md:grid-cols-2">
            <SurfaceCard v-for="item in home.series" :key="item.id" interactive class="flex flex-col">
              <div class="flex items-start justify-between gap-4">
                <div>
                  <p class="eyebrow">{{ item.code }}</p>
                  <h3 class="mt-2 font-display text-xl">{{ item.title }}</h3>
                </div>
                <p class="shrink-0 text-right">
                  <span class="block font-display text-2xl">{{ formatPriceFrom(item.base_price_cents, item.currency) }}</span>
                  <span class="text-xs text-ink-faint">仅展示起价</span>
                </p>
              </div>
              <p class="mt-4 text-sm text-ink-muted">{{ truncate(item.summary, 110) }}</p>
              <dl class="mt-5 grid gap-3 text-sm">
                <div v-if="item.price_includes?.length">
                  <dt class="text-xs text-ink-faint">包含</dt>
                  <dd class="mt-1 flex flex-wrap gap-1.5">
                    <span
                      v-for="line in item.price_includes.slice(0, 5)"
                      :key="line"
                      class="rounded-xs bg-jade-soft px-2 py-0.5 text-xs text-jade"
                    >
                      {{ line }}
                    </span>
                  </dd>
                </div>
                <div v-if="item.price_excludes?.length">
                  <dt class="text-xs text-ink-faint">不含</dt>
                  <dd class="mt-1 flex flex-wrap gap-1.5">
                    <span
                      v-for="line in item.price_excludes.slice(0, 5)"
                      :key="line"
                      class="rounded-xs bg-canvas-sunk px-2 py-0.5 text-xs text-ink-muted"
                    >
                      {{ line }}
                    </span>
                  </dd>
                </div>
                <div v-if="item.lead_time_note">
                  <dt class="text-xs text-ink-faint">交期说明</dt>
                  <dd class="mt-1 text-ink-soft">{{ item.lead_time_note }}</dd>
                </div>
              </dl>
              <div class="mt-6 flex flex-wrap gap-3">
                <AppButton :to="item.route" size="sm" variant="secondary">查看规格</AppButton>
                <AppButton to="/commission/new" size="sm">按此系列询价</AppButton>
              </div>
            </SurfaceCard>
          </div>
          <EmptyState
            v-else
            class="mt-10"
            title="系列资料尚未发布"
            description="系列参数由运营在后台维护并预览后发布；没有核准的参数不会显示。"
          />
        </div>
      </section>

      <!-- 委托步骤：光海印记式清晰流程 -->
      <section class="container-page py-16 lg:py-20">
        <SectionHeading
          eyebrow="How it works"
          title="委托流程"
          description="询价是需求沟通记录，报价是有期限的商业提案，订单才承载实际收款与履约。三者分开保存，每一步你都能看到当前状态。"
        >
          <template #actions>
            <AppButton to="/commission/guide" size="sm">阅读完整指南</AppButton>
          </template>
        </SectionHeading>

        <ol class="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
          <li v-for="step in home.commission_steps" :key="step.step">
            <SurfaceCard class="h-full">
              <p class="font-display text-3xl text-amber">{{ step.step }}</p>
              <h3 class="mt-3 font-display text-lg">{{ step.title }}</h3>
              <p class="mt-3 text-sm leading-relaxed text-ink-muted">{{ step.description }}</p>
            </SurfaceCard>
          </li>
        </ol>

        <div class="mt-8 rounded-lg border border-amber bg-amber-soft p-5 text-sm text-amber-strong">
          报价有效期 {{ site.commissionPolicy.quote_valid_days }} 天、预付款
          {{ site.commissionPolicy.prepayment_percent }}%、尾款 {{ site.commissionPolicy.balance_due_days }} 天、
          免费修改 {{ site.commissionPolicy.free_revision_count }} 次。这些数值由后台配置，页面展示与服务端校验使用同一份配置。
        </div>
      </section>

      <!-- 商城与角色：UL 式系列叙事 + 光海式入口卡 -->
      <section class="border-y border-line bg-surface-muted">
        <div class="container-page py-16 lg:py-20">
          <SectionHeading
            eyebrow="Shop & adoption"
            title="现货、配件与角色领养"
            description="现货与配件直接购买；需要人工审批的掉落与服务先提交申请，通过后按审批通过列表购买；独占角色以真实库存为准。"
          >
            <template #actions>
              <AppButton :to="home.products.more" variant="secondary" size="sm">进入商城</AppButton>
              <AppButton to="/characters" size="sm">角色领养</AppButton>
            </template>
          </SectionHeading>

          <div v-if="home.products.items.length" class="mt-10 grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
            <RouterLink
              v-for="product in home.products.items"
              :key="product.id"
              :to="`/shop/${product.slug}`"
              class="group flex flex-col overflow-hidden rounded-lg border border-line bg-surface transition-shadow hover:shadow-lift"
            >
              <div class="aspect-square overflow-hidden bg-canvas-sunk">
                <img
                  v-if="product.images.length"
                  :src="product.images[0]!.thumb ?? product.images[0]!.src"
                  :alt="product.images[0]!.alt || `${product.title} 商品图`"
                  :width="product.images[0]!.width"
                  :height="product.images[0]!.height"
                  loading="lazy"
                  decoding="async"
                  class="size-full object-cover transition-transform duration-500 group-hover:scale-[1.03]"
                />
                <div v-else class="flex size-full items-center justify-center text-xs text-ink-faint">
                  图片准备中
                </div>
              </div>
              <div class="flex flex-1 flex-col p-4">
                <StatusBadge
                  :tone="product.availability === 'available' ? 'jade' : 'neutral'"
                  :label="product.availability === 'available' ? '有货' : product.availability === 'sold_out' ? '已售罄' : '待上架'"
                />
                <h3 class="mt-3 text-sm font-medium">{{ product.title }}</h3>
                <p class="mt-1 text-xs text-ink-faint">{{ truncate(product.subtitle, 40) }}</p>
                <p class="mt-auto pt-4 font-display text-lg">
                  {{ formatMoney(product.price_from_cents, product.currency === "USD" ? "USD" : "CNY") }}
                  <span class="text-xs text-ink-faint">起</span>
                </p>
              </div>
            </RouterLink>
          </div>
          <EmptyState
            v-else
            class="mt-10"
            title="商城商品正在上架"
            description="上架前需要完成图片授权、SKU 与库存核对，不用占位商品填充页面。"
          >
            <AppButton to="/characters" size="sm" variant="secondary">先看看角色领养</AppButton>
          </EmptyState>
        </div>
      </section>

      <!-- 公告与常见问题 -->
      <section class="container-page grid gap-12 py-16 lg:grid-cols-[1.2fr_1fr] lg:py-20">
        <div>
          <SectionHeading eyebrow="News" title="工作室公告" description="档期开放、新系列上线与展会安排都发布在这里。">
            <template #actions>
              <AppButton :to="home.news.more" variant="secondary" size="sm">全部公告</AppButton>
            </template>
          </SectionHeading>
          <ul v-if="home.news.items.length" class="mt-8 divide-y divide-line border-y border-line">
            <li v-for="item in home.news.items" :key="item.id">
              <RouterLink :to="item.route" class="group flex flex-col gap-2 py-5 sm:flex-row sm:items-baseline sm:gap-6">
                <time class="shrink-0 text-xs tabular-nums text-ink-faint">
                  {{ (item.published_at ?? item.updated_at ?? "").slice(0, 10) }}
                </time>
                <div>
                  <h3 class="font-display text-base group-hover:text-amber-strong">{{ item.title }}</h3>
                  <p class="mt-1 text-sm text-ink-muted">{{ truncate(item.summary, 90) }}</p>
                </div>
              </RouterLink>
            </li>
          </ul>
          <EmptyState v-else class="mt-8" title="暂无公告" description="有新的档期或活动时会在这里发布。" />
        </div>

        <div>
          <SectionHeading eyebrow="FAQ" title="常见问题" description="委托前最常被问到的几件事。" />
          <div v-if="home.faq.length" class="mt-8 space-y-3">
            <details
              v-for="item in home.faq"
              :key="item.id"
              class="group rounded-lg border border-line bg-surface px-5 py-4 open:shadow-quiet"
            >
              <summary class="cursor-pointer list-none font-medium marker:hidden">
                <span class="flex items-start justify-between gap-4">
                  {{ item.title }}
                  <span class="mt-1 shrink-0 text-xs text-ink-faint group-open:hidden" aria-hidden="true">展开</span>
                  <span class="mt-1 hidden shrink-0 text-xs text-ink-faint group-open:inline" aria-hidden="true">收起</span>
                </span>
              </summary>
              <div class="prose-quiet mt-3 text-sm leading-relaxed text-ink-muted" v-html="item.body_html" />
            </details>
          </div>
          <EmptyState v-else class="mt-8" title="FAQ 尚未发布" description="运营整理完成并预览后会公开。" />
          <div class="mt-6">
            <AppButton to="/faq" variant="secondary" size="sm">查看完整 FAQ</AppButton>
          </div>
        </div>
      </section>

      <!-- 联系与经营信息 -->
      <section class="border-t border-line bg-night text-canvas">
        <div class="container-page grid gap-10 py-16 lg:grid-cols-[1.2fr_1fr]">
          <div>
            <p class="eyebrow text-canvas/50">Contact</p>
            <h2 class="mt-3 font-display text-3xl">想开始一个角色？</h2>
            <p class="mt-4 max-w-xl text-sm leading-relaxed text-canvas/70">
              先读委托指南了解可接范围与售后，然后提交询价。人工首响时间为
              {{ site.brand.response_sla.inquiry_workdays }} 个工作日；节假日会另行说明。
            </p>
            <div class="mt-8 flex flex-wrap gap-3">
              <AppButton to="/commission/new" size="lg">提交询价</AppButton>
              <AppButton
                to="/contact"
                variant="secondary"
                size="lg"
                class="!border-night-line !bg-transparent !text-canvas hover:!border-amber-ink hover:!text-amber-ink"
              >
                联系与反馈
              </AppButton>
            </div>
          </div>
          <dl class="grid gap-5 self-center text-sm sm:grid-cols-2">
            <div>
              <dt class="text-canvas/50">经营主体</dt>
              <dd class="mt-1">{{ site.brand.legal_entity }}</dd>
            </div>
            <div>
              <dt class="text-canvas/50">客服邮箱</dt>
              <dd class="mt-1">
                <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
              </dd>
            </div>
            <div>
              <dt class="text-canvas/50">工作时段</dt>
              <dd class="mt-1">{{ site.brand.service_hours }}</dd>
            </div>
            <div>
              <dt class="text-canvas/50">反馈首响</dt>
              <dd class="mt-1">{{ site.brand.response_sla.feedback_workdays }} 个工作日</dd>
            </div>
            <div v-if="home.schedule" class="sm:col-span-2">
              <dt class="text-canvas/50">本轮申请截止</dt>
              <dd class="mt-1 tabular-nums">
                {{ home.schedule.end_at.slice(0, 16).replace("T", " ") }} · 剩余
                {{ formatCountdown(home.schedule.end_at) }}
              </dd>
            </div>
          </dl>
        </div>
      </section>
    </template>
  </div>
</template>

<style scoped>
.prose-quiet :deep(p) {
  margin-bottom: 0.5rem;
}
.prose-quiet :deep(p:last-child) {
  margin-bottom: 0;
}
.prose-quiet :deep(ul) {
  list-style: disc;
  padding-left: 1.25rem;
}
</style>
