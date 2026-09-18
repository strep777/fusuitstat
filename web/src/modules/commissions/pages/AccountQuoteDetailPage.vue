<script setup lang="ts">
/**
 * 询价详情。
 *
 * 规则源：文档/03（`/account/quotes/:id`：补件、撤回、历史报价、有效期、接受报价）、
 * 文档/07（状态机、报价版本、接受时的校验）、文档/11（报价详情信息顺序）。
 *
 * 关键约束：
 * - 过期或旧版本报价不能转订单：QUOTE_EXPIRED / QUOTE_CHANGED 显示明确原因并给“查看最新版本”入口。
 * - 接受时提交 quote_version 与 terms_version；两标签同时接受只会得到一个订单（服务端幂等）。
 * - 按钮禁用只改善体验，不声称保证；真正的判定在服务端。
 * - 补件产生新版本与时间记录，不覆盖工作人员已看到的版本。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  INQUIRY_STATUS_LABEL,
  INQUIRY_STATUS_TONE,
  SUIT_TYPE_OPTIONS,
  acceptQuote,
  addInquirySupplement,
  describeErrorCode,
  fetchInquiry,
  fetchInquiryQuotes,
  inquiryAllowedActions,
  requestQuoteReassessment,
  useDocumentTitle,
  withdrawInquiry,
  type Inquiry,
  type Quote,
  type QuoteAcceptResult,
} from "@/modules/commissions/api/commissions";
import QuoteVersionCard from "@/modules/commissions/components/QuoteVersionCard.vue";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatDateTime, formatMoneyExact } from "@/shared/lib/format";
import { ApiError, newIdempotencyKey } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();
const setTitle = useDocumentTitle();

const inquiryId = computed(() => String(route.params.id ?? ""));

const inquiry = ref<Inquiry | null>(null);
const quotes = ref<Quote[]>([]);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);

const actionError = ref<string | null>(null);
const actionErrorCode = ref("");
const showLatestRequested = ref(false);

const supplementNote = ref("");
const supplementError = ref<string | null>(null);
const supplementing = ref(false);
const showSupplementForm = ref(false);

const withdrawConfirming = ref(false);
const withdrawing = ref(false);

const accepting = ref(false);
const acceptResult = ref<QuoteAcceptResult | null>(null);
const acceptKey = ref<string | null>(null);

const reassessReason = ref("");
const reassessError = ref<string | null>(null);
const reassessing = ref(false);
const showReassessForm = ref(false);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  try {
    const detail = await fetchInquiry(inquiryId.value);
    inquiry.value = detail;
    setTitle(`询价 ${detail.character_name || detail.id}`);
    try {
      quotes.value = await fetchInquiryQuotes(detail.id);
    } catch (caught) {
      // 报价读取失败不影响询价信息展示；单独说明，不把整页标成失败。
      if (!(caught instanceof ApiError) || !caught.needsLogin) {
        actionError.value = "报价版本暂时无法加载，可以稍后重试。";
      }
      quotes.value = [];
    }
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin || caught.status === 403) forbidden.value = true;
      else if (caught.status === 404) notFound.value = true;
      else error.value = caught.message;
    } else {
      error.value = "询价详情加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  setTitle("询价详情");
  void site.load();
  if (!auth.loaded) {
    await auth.loadMe().catch(() => null);
  }
  if (!auth.isAuthenticated) {
    forbidden.value = true;
    loading.value = false;
    return;
  }
  await load();
});

/* ---------------- 报价版本 ---------------- */

const quotesDescending = computed(() => [...quotes.value].sort((a, b) => b.version - a.version));

const latestVersion = computed(() => quotesDescending.value[0]?.version ?? 0);

const alreadyAccepted = computed(
  () => inquiry.value?.status === "ACCEPTED" || quotes.value.some((item) => item.status === "ACCEPTED"),
);

const acceptedQuoteId = computed(
  () => quotes.value.find((item) => item.status === "ACCEPTED")?.id ?? null,
);

/** 当前可接受的版本：服务端 QUOTED 状态下版本号最大的那一份。 */
const currentQuote = computed<Quote | null>(() => {
  if (inquiry.value?.status !== "QUOTED") return null;
  return quotesDescending.value.find((item) => item.status === "PUBLISHED") ?? null;
});

const historyQuotes = computed(() =>
  quotesDescending.value.filter((item) => item.id !== currentQuote.value?.id),
);

const hasNewerVersion = computed(() => !alreadyAccepted.value && historyQuotes.value.length > 0);

function focusLatest(): void {
  showLatestRequested.value = true;
  const element = document.getElementById("quote-current");
  element?.scrollIntoView({ behavior: "smooth", block: "start" });
  element?.focus?.();
}

/* ---------------- 接受报价 ---------------- */

async function onAccept(payload: { quoteId: string; quoteVersion: number; termsVersion: string }): Promise<void> {
  if (accepting.value || acceptResult.value) return;
  actionError.value = null;
  actionErrorCode.value = "";
  accepting.value = true;
  try {
    if (!acceptKey.value) acceptKey.value = newIdempotencyKey();
    const result = await acceptQuote(
      payload.quoteId,
      { quote_version: payload.quoteVersion, terms_version: payload.termsVersion },
      acceptKey.value,
    );
    acceptResult.value = result;
    toast.success("报价已接受", "订单已生成，请在到期前完成首期支付以占用名额。");
    await load();
  } catch (caught) {
    if (caught instanceof ApiError) {
      actionErrorCode.value = caught.code;
      if (caught.needsLogin) {
        actionError.value = "登录状态已过期，请重新登录后再接受报价。";
      } else if (caught.code === "QUOTE_EXPIRED") {
        actionError.value =
          "这份报价已过期，不能转到订单。页面不会按新价格自动处理，请查看最新版本或要求重新评估。";
      } else if (caught.code === "QUOTE_CHANGED") {
        actionError.value =
          "报价内容已被更新，你确认的版本不再是当前版本，因此没有生成订单。请查看最新版本后重新确认。";
      } else if (caught.code === "NO_CAPACITY" || caught.code === "SCHEDULE_CLOSED") {
        actionError.value = describeErrorCode(caught.code);
      } else if (caught.isVersionConflict) {
        actionError.value = "询价已更新，请刷新页面查看最新状态后重试。";
      } else {
        actionError.value = describeErrorCode(caught.code, caught.message);
      }
      // 过期或已变更时立即刷新，让页面展示权威状态。
      if (caught.code === "QUOTE_EXPIRED" || caught.code === "QUOTE_CHANGED") {
        await load();
      }
    } else {
      actionError.value = "接受报价失败，请检查网络后重试。没有生成订单。";
    }
  } finally {
    accepting.value = false;
  }
}

/* ---------------- 补件 ---------------- */

function openSupplement(): void {
  showSupplementForm.value = true;
  supplementError.value = null;
}

async function submitSupplement(): Promise<void> {
  const note = supplementNote.value.trim();
  if (note.length < 5) {
    supplementError.value = "请写清要补充的内容，至少 5 个字。";
    return;
  }
  if (!inquiry.value) return;
  supplementing.value = true;
  supplementError.value = null;
  try {
    const updated = await addInquirySupplement(inquiry.value.id, {
      version: inquiry.value.version,
      note,
    });
    inquiry.value = updated;
    supplementNote.value = "";
    showSupplementForm.value = false;
    toast.success("补件已提交", "补件会产生新的版本记录，工作人员看到的是最新版本。");
  } catch (caught) {
    if (caught instanceof ApiError && caught.isVersionConflict) {
      supplementError.value = "询价已更新，请刷新后再提交补件，避免覆盖已有记录。";
    } else if (caught instanceof ApiError) {
      supplementError.value = describeErrorCode(caught.code, caught.message);
    } else {
      supplementError.value = "补件提交失败，请检查网络后重试。已填写的内容不会丢失。";
    }
  } finally {
    supplementing.value = false;
  }
}

/* ---------------- 撤回 ---------------- */

async function confirmWithdraw(): Promise<void> {
  if (!inquiry.value || withdrawing.value) return;
  withdrawing.value = true;
  actionError.value = null;
  try {
    const updated = await withdrawInquiry(inquiry.value.id, inquiry.value.version);
    inquiry.value = updated;
    withdrawConfirming.value = false;
    toast.info("询价已撤回", "终态记录会保留，你不会因此被收费；可以重新提交新的询价。");
  } catch (caught) {
    if (caught instanceof ApiError && caught.isVersionConflict) {
      actionError.value = "询价已更新，请刷新后重新确认是否撤回。";
    } else if (caught instanceof ApiError) {
      actionError.value = describeErrorCode(caught.code, caught.message);
    } else {
      actionError.value = "撤回失败，请检查网络后重试。";
    }
  } finally {
    withdrawing.value = false;
  }
}

/* ---------------- 要求重新评估 ---------------- */

async function submitReassess(): Promise<void> {
  const reason = reassessReason.value.trim();
  if (reason.length < 5) {
    reassessError.value = "请说明希望重新评估的原因，至少 5 个字。";
    return;
  }
  if (!inquiry.value) return;
  reassessing.value = true;
  reassessError.value = null;
  try {
    const updated = await requestQuoteReassessment(inquiry.value.id, {
      version: inquiry.value.version,
      reason,
    });
    inquiry.value = updated;
    reassessReason.value = "";
    showReassessForm.value = false;
    toast.info("已请求重新评估", "工作人员会重新查看需求并发布新的报价版本。");
  } catch (caught) {
    if (caught instanceof ApiError && caught.isVersionConflict) {
      reassessError.value = "询价已更新，请刷新后重试。";
    } else if (caught instanceof ApiError) {
      reassessError.value = describeErrorCode(caught.code, caught.message);
    } else {
      reassessError.value = "请求失败，请检查网络后重试。";
    }
  } finally {
    reassessing.value = false;
  }
}

const allowed = computed(() => (inquiry.value ? inquiryAllowedActions(inquiry.value.status) : []));

const suitTypeLabel = computed(() => {
  const value = inquiry.value?.suit_type ?? "";
  return SUIT_TYPE_OPTIONS.find((item) => item.value === value)?.label ?? value;
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '询价与报价', to: '/account/quotes' },
        { label: '详情' },
      ]"
    />

    <div v-if="loading" class="mt-8">
      <StarLoader message="正在读取询价与报价" hint="包含历史报价版本" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="需要登录后查看"
      description="询价与报价只对本人开放。登录后会回到这一页。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/quotes">返回列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="notFound"
      class="mt-8"
      tone="not-found"
      title="没有找到这份询价"
      description="它可能属于其他账号，或者编号不正确。列表里只会显示你自己的询价。"
    >
      <AppButton size="sm" to="/account/quotes">返回列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="询价详情加载失败"
      :description="`${error} 可以重试；已提交的询价不会因为打不开页面而丢失。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/quotes">返回列表</AppButton>
    </EmptyState>

    <template v-else-if="inquiry">
      <header class="mt-6 max-w-3xl">
        <div class="flex flex-wrap items-center gap-2">
          <StatusBadge :tone="INQUIRY_STATUS_TONE[inquiry.status]" :label="INQUIRY_STATUS_LABEL[inquiry.status]" />
          <StatusBadge tone="neutral" :label="`编号 ${inquiry.id}`" />
          <StatusBadge tone="neutral" :label="`版本 ${inquiry.version}`" />
        </div>
        <h1 class="mt-4 font-display text-3xl leading-tight">
          {{ inquiry.character_name || "未命名角色" }}
          <span class="text-lg text-ink-faint">· {{ inquiry.species || "未填物种" }}</span>
        </h1>
        <p class="mt-3 text-sm text-ink-muted">
          {{ suitTypeLabel }} · 收货区域 {{ inquiry.delivery_region || "未填写" }} · 提交于
          {{ inquiry.submitted_at ? formatDateTime(inquiry.submitted_at) : "尚未提交" }}
        </p>
      </header>

      <!-- 接受成功 -->
      <SurfaceCard v-if="acceptResult" class="mt-8">
        <div class="flex flex-wrap items-center gap-3">
          <StatusBadge tone="jade" label="已接受报价" />
          <StatusBadge tone="neutral" :label="`订单 ${acceptResult.order_id}`" />
        </div>
        <h2 class="mt-4 font-display text-xl">订单已生成，请在到期前完成首期支付</h2>
        <dl class="mt-5 grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt class="text-ink-faint">首期金额</dt>
            <dd class="mt-1 font-display text-lg tabular-nums">
              {{ formatMoneyExact(acceptResult.amount_cents, acceptResult.currency === "USD" ? "USD" : "CNY") }}
            </dd>
          </div>
          <div>
            <dt class="text-ink-faint">支付截止</dt>
            <dd class="mt-1 tabular-nums text-ink-soft">{{ formatDateTime(acceptResult.expires_at) }}</dd>
          </div>
          <div>
            <dt class="text-ink-faint">款项编号</dt>
            <dd class="mt-1 font-mono text-xs text-ink-soft">{{ acceptResult.installment_id }}</dd>
          </div>
        </dl>
        <p class="mt-4 rounded-sm bg-canvas-sunk px-4 py-3 text-xs text-ink-muted">
          重复提交或同时打开两个标签页接受，只会得到一个订单；这里显示的订单号就是权威结果。
          支付完成后名额才转为正式占用。
        </p>
        <div class="mt-5 flex flex-wrap gap-3">
          <AppButton :to="`/account/orders/${acceptResult.order_id}`">查看订单</AppButton>
          <AppButton variant="secondary" to="/account/quotes">返回询价列表</AppButton>
        </div>
      </SurfaceCard>

      <!-- 动作错误 -->
      <div v-if="actionError" class="mt-6 rounded-lg border border-wine bg-wine-soft p-5 text-sm text-wine" role="alert">
        <p class="font-medium">本次操作没有完成</p>
        <p class="mt-2">{{ actionError }}</p>
        <p v-if="actionErrorCode" class="mt-2 text-xs">错误码：{{ actionErrorCode }}</p>
        <div class="mt-4 flex flex-wrap gap-3">
          <AppButton v-if="hasNewerVersion" size="sm" variant="secondary" @click="focusLatest">查看最新版本</AppButton>
          <AppButton size="sm" variant="secondary" @click="load()">刷新页面</AppButton>
          <AppButton size="sm" variant="ghost" to="/contact">联系客服</AppButton>
        </div>
      </div>

      <!-- 状态说明与允许动作 -->
      <div class="mt-8 grid gap-6 lg:grid-cols-[1.3fr_0.7fr]">
        <div class="space-y-6">
          <SurfaceCard>
            <h2 class="font-display text-lg">询价内容</h2>
            <dl class="mt-4 divide-y divide-line text-sm">
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">系列</dt>
                <dd class="text-ink-soft">{{ inquiry.series_id || "未填写" }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">制作类型</dt>
                <dd class="text-ink-soft">{{ suitTypeLabel }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">设定图</dt>
                <dd class="text-ink-soft">{{ (inquiry.design_asset_ids ?? []).length }} 张（私密附件）</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">预算区间</dt>
                <dd class="text-ink-soft tabular-nums">
                  {{ inquiry.budget_min_cents === null ? "未填" : formatMoneyExact(inquiry.budget_min_cents) }} –
                  {{ inquiry.budget_max_cents === null ? "未填" : formatMoneyExact(inquiry.budget_max_cents) }}
                </dd>
              </div>
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">意向日期</dt>
                <dd class="text-ink-soft tabular-nums">{{ inquiry.target_date || "未填写" }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">联系偏好</dt>
                <dd class="text-ink-soft">{{ inquiry.contact_preference || "未指定（使用账号手机号）" }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-3 py-2.5">
                <dt class="text-ink-faint">设定授权确认</dt>
                <dd class="text-ink-soft">{{ inquiry.rights_declaration ? "已确认" : "未确认" }}</dd>
              </div>
            </dl>
            <div v-if="inquiry.requirements" class="mt-4">
              <p class="text-xs text-ink-faint">需求说明</p>
              <p class="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">{{ inquiry.requirements }}</p>
            </div>
            <p v-if="inquiry.needs_info_note" class="mt-4 rounded-sm bg-wine-soft px-3 py-2 text-sm text-wine">
              待补件说明：{{ inquiry.needs_info_note }}
            </p>
            <p v-if="inquiry.reject_reason" class="mt-4 rounded-sm bg-canvas-sunk px-3 py-2 text-sm text-ink-muted">
              未承接原因：{{ inquiry.reject_reason }}
            </p>
            <p class="mt-4 text-xs text-ink-faint">
              量体信息不在询价里收集，进入制作阶段后会单独建立量体版本。
            </p>
          </SurfaceCard>

          <!-- 当前可接受报价 -->
          <div v-if="currentQuote" id="quote-current" tabindex="-1">
            <h2 class="mb-3 font-display text-lg">当前可接受的报价</h2>
            <QuoteVersionCard
              :quote="currentQuote"
              current
              :has-newer-version="false"
              :accepting="accepting"
              @accept="onAccept"
            />
          </div>

          <!-- 已接受报价 -->
          <div v-else-if="alreadyAccepted">
            <h2 class="mb-3 font-display text-lg">已接受的报价</h2>
            <QuoteVersionCard
              v-if="quotesDescending.find((item) => item.id === acceptedQuoteId) ?? quotesDescending[0]"
              :quote="(quotesDescending.find((item) => item.id === acceptedQuoteId) ?? quotesDescending[0])!"
              accepted
              :has-newer-version="false"
              @view-latest="focusLatest"
            />
          </div>

          <EmptyState
            v-else-if="quotesDescending.length === 0"
            :title="inquiry.status === 'DRAFT' ? '还没有提交询价' : '还没有收到报价'"
            :description="
              inquiry.status === 'DRAFT'
                ? '草稿不会自动提交给工作人员。继续填写并提交后才会进入人工处理。'
                : `工作人员会按人工响应时间处理（${site.brand.response_sla.inquiry_workdays} 个工作日）。发布报价前不会发送可支付链接。`
            "
          >
            <AppButton
              v-if="inquiry.status === 'DRAFT'"
              size="sm"
              :to="`/commission/new?draft=${inquiry.id}`"
            >
              继续填写草稿
            </AppButton>
          </EmptyState>

          <!-- 历史报价版本 -->
          <section v-if="historyQuotes.length > 0" class="space-y-4">
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-lg">历史报价版本</h2>
              <StatusBadge tone="neutral" :label="`共 ${historyQuotes.length} 个旧版本`" />
            </div>
            <p class="text-xs text-ink-faint">
              旧版本仅用于对照：它们不能转成订单。若中途改价，接受旧版本会返回 QUOTE_CHANGED，不会被静默按新价处理。
            </p>
            <div v-if="showLatestRequested" class="rounded-lg border border-amber bg-amber-soft p-3 text-xs text-amber-strong">
              最新版本在上方“当前可接受的报价”区域；如果没有该区域，说明当前没有可接受的报价版本。
            </div>
            <QuoteVersionCard
              v-for="quote in historyQuotes"
              :key="quote.id"
              :quote="quote"
              :current="false"
              :has-newer-version="quote.version < latestVersion"
              :accepted="quote.status === 'ACCEPTED'"
              @view-latest="focusLatest"
              @accept="onAccept"
            />
          </section>
        </div>

        <!-- 侧栏：允许动作 -->
        <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <SurfaceCard>
            <h2 class="font-display text-base">现在可以做什么</h2>
            <ul class="mt-3 space-y-1.5 text-xs text-ink-muted">
              <li v-if="allowed.includes('edit')">继续编辑草稿（未提交前不会进入人工排队）。</li>
              <li v-if="allowed.includes('submit')">提交给工作人员进入评估。</li>
              <li v-if="allowed.includes('supplement')">补齐工作人员要求的资料。</li>
              <li v-if="allowed.includes('accept')">在有效期内接受当前版本报价。</li>
              <li v-if="allowed.includes('withdraw')">撤回询价（终态保留记录）。</li>
              <li v-if="allowed.length === 0">当前状态没有可执行动作，请按下方说明等待或联系客服。</li>
            </ul>

            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton
                v-if="allowed.includes('edit')"
                size="sm"
                variant="secondary"
                :to="`/commission/new?draft=${inquiry.id}`"
              >
                继续编辑
              </AppButton>
              <AppButton v-if="allowed.includes('supplement')" size="sm" @click="openSupplement">提交补件</AppButton>
              <AppButton v-if="inquiry.status === 'QUOTED'" size="sm" variant="secondary" @click="showReassessForm = true">
                要求重新评估
              </AppButton>
            </div>

            <div v-if="allowed.includes('withdraw')" class="mt-5 border-t border-line pt-4">
              <p class="text-xs text-ink-faint">
                撤回后这条记录进入终态，不会删除；需要继续时请重新提交新的询价并关联原单。
              </p>
              <div v-if="!withdrawConfirming" class="mt-3">
                <AppButton size="sm" variant="danger" @click="withdrawConfirming = true">撤回询价</AppButton>
              </div>
              <div v-else class="mt-3 rounded-sm border border-wine bg-wine-soft p-3">
                <p class="text-xs text-wine">
                  确认撤回编号 {{ inquiry.id }} 的询价？撤回后工作人员不再按这条记录报价。
                </p>
                <div class="mt-3 flex flex-wrap gap-2">
                  <AppButton size="sm" variant="danger" :loading="withdrawing" @click="confirmWithdraw">
                    确认撤回
                  </AppButton>
                  <AppButton size="sm" variant="secondary" @click="withdrawConfirming = false">取消</AppButton>
                </div>
              </div>
            </div>
          </SurfaceCard>

          <!-- 补件表单 -->
          <SurfaceCard v-if="showSupplementForm">
            <h2 class="font-display text-base">提交补件</h2>
            <FieldRow
              label="补充说明"
              for="supplement-note"
              required
              :error="supplementError ?? ''"
              hint="可写补充尺寸、结构要求或对应问题的回答。补件会生成新的版本记录。"
            >
              <textarea id="supplement-note" v-model="supplementNote" class="field-base min-h-[96px]" maxlength="2000" />
            </FieldRow>
            <div class="mt-3 flex flex-wrap gap-2">
              <AppButton size="sm" :loading="supplementing" @click="submitSupplement">提交补件</AppButton>
              <AppButton size="sm" variant="secondary" @click="showSupplementForm = false">取消</AppButton>
            </div>
          </SurfaceCard>

          <!-- 重新评估 -->
          <SurfaceCard v-if="showReassessForm">
            <h2 class="font-display text-base">要求重新评估</h2>
            <FieldRow
              label="原因"
              for="reassess-reason"
              required
              :error="reassessError ?? ''"
              hint="例如预算区间调整、结构方案变化。重新评估会发布新的报价版本，旧版本自动作废。"
            >
              <textarea id="reassess-reason" v-model="reassessReason" class="field-base min-h-[96px]" maxlength="1000" />
            </FieldRow>
            <div class="mt-3 flex flex-wrap gap-2">
              <AppButton size="sm" :loading="reassessing" @click="submitReassess">提交请求</AppButton>
              <AppButton size="sm" variant="secondary" @click="showReassessForm = false">取消</AppButton>
            </div>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">报价与订单的区别</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">
              报价是有期限的商业提案；订单才承载实际收款与履约。接受报价时会校验当前版本、有效期、
              归属与名额资格，写入订单与款项快照。
            </p>
            <p class="mt-2 text-xs text-ink-faint">
              报价有效期 {{ site.commissionPolicy.quote_valid_days }} 天、首期
              {{ site.commissionPolicy.prepayment_percent }}%、尾款
              {{ site.commissionPolicy.balance_due_days }} 天，均由后台配置。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">需要帮助</h2>
            <p class="mt-3 text-xs text-ink-muted">
              对报价明细、补件要求或时间安排有疑问，可以直接联系客服；工作时段
              {{ site.brand.service_hours }}。
            </p>
            <div class="mt-3 flex flex-wrap gap-2">
              <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
              <AppButton size="sm" variant="ghost" to="/commission/contract">条款说明</AppButton>
            </div>
          </SurfaceCard>
        </aside>
      </div>
    </template>
  </div>
</template>
