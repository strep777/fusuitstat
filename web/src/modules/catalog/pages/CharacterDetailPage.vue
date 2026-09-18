<script setup lang="ts">
/**
 * 角色详情（/characters/:slug）：设定图、作者、故事、授权说明、价格与申请/购买入口。
 *
 * 约束（03/C12 文档）：
 * - 已售或保留（status != available）时禁用购买并写明原因，不靠颜色单独表达。
 * - 独占角色提示“库存 1”，同一角色不能售出两次；purchasable 由服务端裁定。
 * - 授权范围只显示运营已发布的内容；缺失时说明“按合同要求执行”，不自行扩写。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute } from "vue-router";

import {
  applyPageMeta,
  characterStatusMeta,
  fetchCharacter,
  toCurrency,
  withQuery,
  type CharacterDetail,
} from "@/modules/catalog/api/catalog";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatMoney, truncate } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const route = useRoute();
const detail = ref<CharacterDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const notFound = ref(false);
const forbidden = ref(false);
const activeImage = ref(0);

const slug = computed(() => String(route.params.slug ?? ""));

const images = computed(() => detail.value?.images ?? []);
const currentImage = computed(() => images.value[activeImage.value] ?? detail.value?.cover ?? null);
const status = computed(() => characterStatusMeta(detail.value?.status));
const canPurchase = computed(
  () => status.value.open && detail.value?.purchasable === true && Boolean(detail.value?.product_slug),
);

/** 不可购买时的可见原因：状态原因优先，其次才是服务端可售标记。 */
const blockedReason = computed(() => {
  if (!detail.value) return "";
  if (!status.value.open) return status.value.blockedReason;
  if (detail.value.purchasable !== true) {
    return "服务端标记该角色当前不可购买（可能已被占用或下架），可售性以下单校验为准。";
  }
  if (!detail.value.product_slug) {
    return "该角色尚未关联可售商品，暂时不能在线购买；可先提交领养申请。";
  }
  return "";
});

/** 申请入口：带商品与角色参数；缺少 product_id 时用 product_slug 让申请页自行解析。 */
const applicationUrl = computed(() =>
  withQuery("/account/applications", {
    kind: "drop",
    product_id: detail.value?.product_id ?? "",
    product_slug: detail.value?.product_id ? "" : detail.value?.product_slug ?? "",
    character: detail.value?.slug ?? "",
  }),
);

async function load(): Promise<void> {
  if (!slug.value) {
    notFound.value = true;
    loading.value = false;
    return;
  }
  loading.value = true;
  error.value = null;
  notFound.value = false;
  forbidden.value = false;
  activeImage.value = 0;
  try {
    const payload = await fetchCharacter(slug.value);
    detail.value = payload;
    applyPageMeta({
      title: payload.name,
      description:
        payload.summary ||
        truncate(payload.story ?? "", 120) ||
        `${payload.name} 的设定图、作者署名、领养状态与授权说明。`,
      canonicalPath: `/characters/${payload.slug}`,
    });
  } catch (caught) {
    if (caught instanceof ApiError && (caught.status === 404 || caught.code === "RESOURCE_NOT_FOUND")) {
      notFound.value = true;
    } else if (caught instanceof ApiError && (caught.status === 403 || caught.needsLogin)) {
      forbidden.value = true;
    } else {
      error.value = caught instanceof ApiError ? caught.message : "角色详情加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(() => {
  applyPageMeta({
    title: "角色详情",
    description: "角色设定图、作者署名、领养状态、价格与授权说明。",
    canonicalPath: `/characters/${slug.value}`,
  });
  void load();
});

watch(slug, () => void load());
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '角色领养', to: '/characters' },
        { label: detail?.name ?? '角色详情' },
      ]"
    />

    <div v-if="loading" class="py-16">
      <StarLoader message="正在读取角色设定" />
    </div>

    <EmptyState
      v-else-if="notFound"
      class="mt-10"
      tone="not-found"
      title="找不到这个角色"
      description="该角色可能已被下架或地址有误；已领养角色是否继续展示由授权与运营决定。"
    >
      <AppButton to="/characters" size="sm">返回角色列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="forbidden"
      class="mt-10"
      tone="forbidden"
      title="没有访问该角色的权限"
      description="该角色可能仅对特定客户开放。公开角色无需登录即可查看，登录后可确认账号状态。"
    >
      <AppButton to="/login" size="sm">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/characters">返回角色列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-10"
      tone="error"
      title="角色详情暂时无法加载"
      :description="`${error} 网络恢复后可以重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/characters">返回角色列表</AppButton>
    </EmptyState>

    <template v-else-if="detail">
      <div class="mt-6 grid gap-10 lg:grid-cols-[1.05fr_0.95fr]">
        <!-- 设定图 -->
        <div>
          <div class="overflow-hidden rounded-lg border border-line bg-canvas-sunk">
            <img
              v-if="currentImage"
              :src="currentImage.thumb ?? currentImage.src"
              :srcset="currentImage.srcset?.map((item) => `${item.url} ${item.width}w`).join(', ') || undefined"
              sizes="(min-width: 1024px) 50vw, 100vw"
              :width="currentImage.width"
              :height="currentImage.height"
              :alt="currentImage.alt || `${detail.name} 设定图`"
              loading="lazy"
              decoding="async"
              class="w-full object-contain"
            />
            <div v-else class="flex aspect-[4/5] items-center justify-center text-xs text-ink-faint">
              设定图准备中
            </div>
          </div>

          <div v-if="images.length > 1" class="mt-3 flex flex-wrap gap-3">
            <button
              v-for="(image, index) in images"
              :key="image.id"
              type="button"
              class="overflow-hidden rounded-sm border transition-colors"
              :class="index === activeImage ? 'border-amber' : 'border-line hover:border-amber'"
              :aria-label="`查看第 ${index + 1} 张设定图`"
              :aria-pressed="index === activeImage"
              @click="activeImage = index"
            >
              <img
                :src="image.thumb ?? image.src"
                :width="image.width"
                :height="image.height"
                :alt="image.alt || `${detail.name} 设定图 ${index + 1}`"
                loading="lazy"
                decoding="async"
                class="size-20 object-cover"
              />
            </button>
          </div>
        </div>

        <!-- 状态、价格与申请入口 -->
        <div>
          <div class="flex flex-wrap items-center gap-2">
            <StatusBadge :tone="status.tone" :label="status.label" />
            <StatusBadge v-if="detail.exclusive" tone="night" label="独占 · 库存 1" />
            <StatusBadge v-if="detail.species" tone="neutral" :label="detail.species" />
          </div>

          <h1 class="mt-4 font-display text-3xl leading-tight sm:text-4xl">{{ detail.name }}</h1>
          <p v-if="detail.designer_credit" class="mt-3 text-sm text-ink-muted">
            设定作者：{{ detail.designer_credit }}
          </p>

          <div class="mt-6 rounded-lg border border-line bg-surface-muted p-5">
            <p class="eyebrow">Price</p>
            <p class="mt-2 font-display text-3xl">
              <template v-if="detail.price_cents !== null">
                {{ formatMoney(detail.price_cents, toCurrency(detail.currency)) }}
              </template>
              <span v-else class="text-lg text-ink-muted">售价待公布</span>
            </p>
            <p class="mt-2 text-xs text-ink-faint">
              售价不含运费；授权范围、交付内容与交期以合同和报价记录为准。
            </p>
            <p v-if="detail.exclusive" class="mt-3 text-xs text-ink-muted">
              独占角色库存固定为 1，售出后不再复制；页面价格只作展示，实际以下单时服务端校验为准。
            </p>
          </div>

          <!-- 购买/申请：不可购买时禁用并在按钮旁说明原因 -->
          <div class="mt-6 flex flex-wrap gap-3">
            <AppButton
              v-if="canPurchase"
              :to="`/shop/${detail.product_slug}`"
              size="lg"
            >
              前往购买
            </AppButton>
            <AppButton
              v-else
              size="lg"
              disabled
              :disabled-reason="blockedReason"
            >
              暂不可购买
            </AppButton>

            <AppButton :to="applicationUrl" size="lg" variant="secondary">提交领养申请</AppButton>
          </div>

          <p v-if="blockedReason" class="mt-3 text-sm text-ink-muted" role="status">
            {{ blockedReason }}
          </p>

          <div v-if="detail.adoption_note" class="mt-6 rounded-lg border border-line bg-surface p-5 text-sm">
            <p class="eyebrow">Adoption</p>
            <p class="mt-2 leading-relaxed text-ink-soft">{{ detail.adoption_note }}</p>
          </div>

          <div v-if="detail.tags?.length" class="mt-6 flex flex-wrap gap-2">
            <StatusBadge v-for="tag in detail.tags" :key="tag" tone="neutral" :label="tag" />
          </div>
        </div>
      </div>

      <!-- 故事与授权 -->
      <section class="mt-16 grid gap-6 md:grid-cols-2">
        <SurfaceCard>
          <h2 class="font-display text-xl">角色故事</h2>
          <p v-if="detail.story" class="mt-3 whitespace-pre-line text-sm leading-relaxed text-ink-muted">
            {{ detail.story }}
          </p>
          <p v-else class="mt-3 text-sm text-ink-muted">
            该角色的故事尚未发布。设定细节可以在询价时直接沟通，页面不用占位文案填充。
          </p>
        </SurfaceCard>

        <SurfaceCard>
          <h2 class="font-display text-xl">授权说明</h2>
          <p v-if="detail.rights_summary" class="mt-3 whitespace-pre-line text-sm leading-relaxed text-ink-muted">
            {{ detail.rights_summary }}
          </p>
          <p v-else class="mt-3 text-sm text-ink-muted">
            该角色尚未发布授权摘要。授权范围按照合同要求执行，合同版本与签署记录保存在订单内。
          </p>
          <p class="mt-3 text-xs text-ink-faint">
            领养不代表取得原设定著作权；可商用范围、二次创作与展示授权以合同条款为准。
          </p>
        </SurfaceCard>
      </section>

      <section class="mt-16">
        <SectionHeading
          eyebrow="Next step"
          title="确认要这个角色？"
          description="先提交领养申请说明用途，工作人员会核对状态与授权范围；可购买时再进入商品页选择 SKU 下单。"
        >
          <template #actions>
            <AppButton :to="applicationUrl" size="sm">提交领养申请</AppButton>
            <AppButton to="/characters" size="sm" variant="secondary">继续浏览角色</AppButton>
          </template>
        </SectionHeading>
      </section>
    </template>
  </div>
</template>
