<script setup lang="ts">
/**
 * 反馈详情。
 *
 * 规则源：文档/03（反馈列表与奖励结果、申诉）、文档/09（反馈处理与奖励状态）。
 *
 * 关键约束：
 * - 展示处理进度、奖励/不奖励理由与申诉入口。
 * - 不要求用户删除差评、给好评或说正面内容；页面写明这一点。
 * - 私人内容只读自己的反馈；附件通过短期访问地址按需获取。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { fetchMediaAccess, useDocumentTitle } from "@/modules/commissions/api/commissions";
import {
  FEEDBACK_CATEGORY_LABEL,
  FEEDBACK_FAIRNESS_NOTICE,
  FEEDBACK_PRIVACY_NOTICE,
  FEEDBACK_STATUS_LABEL,
  FEEDBACK_STATUS_STEPS,
  REWARD_STATUS_LABEL,
  createFeedbackAppeal,
  fetchFeedback,
  postFeedbackMessage,
  type FeedbackDetail,
} from "@/modules/feedback/api/feedback";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatDateTime } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();
const setTitle = useDocumentTitle();

const feedbackId = computed(() => String(route.params.id ?? ""));

const feedback = ref<FeedbackDetail | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);

const messageDraft = ref("");
const messageError = ref<string | null>(null);
const messageSaving = ref(false);

const appealDraft = ref("");
const appealError = ref<string | null>(null);
const appealSaving = ref(false);
const appealOpen = ref(false);

const mediaUrls = ref<Record<string, string>>({});
const mediaLoading = ref<string | null>(null);
const mediaError = ref<Record<string, string>>({});

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  try {
    const detail = await fetchFeedback(feedbackId.value);
    feedback.value = detail;
    setTitle(`反馈 ${detail.title}`);
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin || caught.status === 403) forbidden.value = true;
      else if (caught.status === 404) notFound.value = true;
      else error.value = caught.message;
    } else {
      error.value = "反馈详情加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  setTitle("反馈详情");
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

const statusLabel = computed(() => FEEDBACK_STATUS_LABEL[feedback.value?.status ?? ""] ?? feedback.value?.status ?? "");

/** 终态反馈不再计时（与服务端 `feedback_sla` 的口径一致）。 */
const isSettledStatus = computed(() =>
  ["CLOSED", "REJECTED", "DUPLICATE"].includes(String(feedback.value?.status ?? "")),
);

const rewardLabel = computed(
  () => REWARD_STATUS_LABEL[feedback.value?.reward_status ?? ""] ?? feedback.value?.reward_status ?? "",
);

const statusTone = computed(() => {
  const status = feedback.value?.status ?? "";
  if (status === "RESOLVED" || status === "CLOSED") return "jade" as const;
  if (status === "REJECTED" || status === "DUPLICATE") return "wine" as const;
  if (status === "IN_PROGRESS" || status === "TRIAGED") return "amber" as const;
  return "neutral" as const;
});

/** 非线性的终态不套用步骤条，避免把“重复/未采纳”说成处理中的一个环节。 */
const isExceptionalStatus = computed(
  () => feedback.value?.status === "DUPLICATE" || feedback.value?.status === "REJECTED",
);

const currentStepIndex = computed(() => {
  const status = feedback.value?.status ?? "";
  const normalized = status === "DUPLICATE" || status === "REJECTED" ? "CLOSED" : status;
  const index = FEEDBACK_STATUS_STEPS.findIndex((step) => step.value === normalized);
  if (index < 0) return 0;
  return Math.min(index, FEEDBACK_STATUS_STEPS.length - 1);
});

const needsInfo = computed(() => feedback.value?.status === "NEEDS_INFO");

async function toggleMedia(assetId: string): Promise<void> {
  if (mediaUrls.value[assetId]) {
    const next = { ...mediaUrls.value };
    delete next[assetId];
    mediaUrls.value = next;
    return;
  }
  mediaLoading.value = assetId;
  const clearedError = { ...mediaError.value };
  delete clearedError[assetId];
  mediaError.value = clearedError;
  try {
    const access = await fetchMediaAccess(assetId);
    mediaUrls.value = { ...mediaUrls.value, [assetId]: access.url };
  } catch (caught) {
    mediaError.value = {
      ...mediaError.value,
      [assetId]: caught instanceof ApiError ? `无法获取附件：${caught.message}` : "无法获取附件，请稍后重试。",
    };
  } finally {
    mediaLoading.value = null;
  }
}

async function sendMessage(): Promise<void> {
  const body = messageDraft.value.trim();
  if (body.length < 2) {
    messageError.value = "请写清要补充的内容，至少 2 个字。";
    return;
  }
  if (!feedback.value) return;
  messageSaving.value = true;
  messageError.value = null;
  try {
    const message = await postFeedbackMessage(feedback.value.id, { body });
    feedback.value = { ...feedback.value, messages: [...feedback.value.messages, message] };
    messageDraft.value = "";
    toast.success("补充已发送", "工作人员会看到这条补充；补充关键新证据可以作为同一条反馈升级奖励。");
  } catch (caught) {
    messageError.value =
      caught instanceof ApiError ? caught.message : "发送失败，请检查网络后重试，已填写的内容不会丢失。";
  } finally {
    messageSaving.value = false;
  }
}

async function submitAppeal(): Promise<void> {
  const reason = appealDraft.value.trim();
  if (reason.length < 10) {
    appealError.value = "请写清你的依据或疑问，至少 10 个字。";
    return;
  }
  if (!feedback.value) return;
  appealSaving.value = true;
  appealError.value = null;
  try {
    const appeal = await createFeedbackAppeal(feedback.value.id, reason);
    feedback.value = { ...feedback.value, appeals: [...feedback.value.appeals, appeal] };
    appealDraft.value = "";
    appealOpen.value = false;
    toast.success("申诉已提交", "审核结论会连同理由一起更新在这里。");
  } catch (caught) {
    appealError.value = caught instanceof ApiError ? caught.message : "申诉提交失败，请检查网络后重试。";
  } finally {
    appealSaving.value = false;
  }
}
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '我的反馈', to: '/account/feedback' },
        { label: '详情' },
      ]"
    />

    <div v-if="loading" class="mt-8">
      <StarLoader message="正在读取反馈记录" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="需要登录后查看"
      description="反馈记录只对本人开放。登录后会回到这一页。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/feedback">返回列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="notFound"
      class="mt-8"
      tone="not-found"
      title="没有找到这条反馈"
      description="它可能属于其他账号，或者编号不正确。列表里只会显示你自己提交的反馈。"
    >
      <AppButton size="sm" to="/account/feedback">返回列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="反馈详情加载失败"
      :description="`${error} 可以重试；反馈记录不会因为打不开页面而丢失。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/feedback">返回列表</AppButton>
    </EmptyState>

    <template v-else-if="feedback">
      <header class="mt-6 max-w-3xl">
        <div class="flex flex-wrap items-center gap-2">
          <StatusBadge tone="neutral" :label="FEEDBACK_CATEGORY_LABEL[feedback.category] ?? feedback.category" />
          <StatusBadge :tone="statusTone" :label="statusLabel" />
          <StatusBadge tone="neutral" :label="`奖励：${rewardLabel}`" />
          <StatusBadge tone="neutral" :label="`编号 ${feedback.id}`" />
        </div>
        <h1 class="mt-4 font-display text-3xl leading-tight">{{ feedback.title }}</h1>
        <p class="mt-3 text-sm text-ink-muted">
          提交于 {{ formatDateTime(feedback.created_at) }} · 最近更新 {{ formatDateTime(feedback.updated_at) }}
        </p>
        <!-- 首响时效（C30：反馈 7 个工作日）与询价侧同一口径：三种状态如实呈现 -->
        <p v-if="feedback.response_due_at && !isSettledStatus" class="mt-2 text-sm">
          <span v-if="feedback.first_responded_at" class="text-jade">
            工作人员已于 {{ formatDateTime(feedback.first_responded_at) }} 首次响应
            （承诺 {{ feedback.sla_workdays }} 个工作日）。
          </span>
          <span v-else-if="feedback.response_overdue" class="text-wine">
            首响承诺为 {{ feedback.sla_workdays }} 个工作日，截止
            {{ formatDateTime(feedback.response_due_at) }} 仍未响应；我们正在跟进，也可直接联系客服催办。
          </span>
          <span v-else>
            首响承诺 {{ feedback.sla_workdays }} 个工作日，本次截止
            {{ formatDateTime(feedback.response_due_at) }}（按客服工作日计算，节假日顺延）。
          </span>
        </p>
        <p v-else-if="isSettledStatus" class="mt-2 text-sm text-ink-muted">
          该反馈已有处理结论，首响时效不再计时。
        </p>
        <p class="mt-2 text-xs text-ink-faint">
          页面路径：<span class="font-mono">{{ feedback.page_path }}</span>
        </p>
      </header>

      <div class="mt-8 grid gap-6 lg:grid-cols-[1.35fr_0.65fr]">
        <div class="space-y-6">
          <SurfaceCard>
            <h2 class="font-display text-lg">反馈内容</h2>
            <p class="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">{{ feedback.body }}</p>
            <div v-if="feedback.steps" class="mt-5">
              <h3 class="text-xs text-ink-faint">复现步骤</h3>
              <p class="mt-1 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">{{ feedback.steps }}</p>
            </div>
            <div v-if="feedback.asset_ids.length" class="mt-5">
              <h3 class="text-xs text-ink-faint">附件 {{ feedback.asset_ids.length }} 张</h3>
              <div class="mt-2 flex flex-wrap gap-2">
                <AppButton
                  v-for="assetId in feedback.asset_ids"
                  :key="assetId"
                  size="sm"
                  variant="secondary"
                  :loading="mediaLoading === assetId"
                  @click="toggleMedia(assetId)"
                >
                  {{ mediaUrls[assetId] ? "收起附件" : "查看附件" }}
                </AppButton>
              </div>
              <div class="mt-3 space-y-3">
                <template v-for="assetId in feedback.asset_ids" :key="assetId">
                  <figure v-if="mediaUrls[assetId]" class="overflow-hidden rounded-sm border border-line">
                    <img :src="mediaUrls[assetId]" alt="反馈附件图片" loading="lazy" decoding="async" class="w-full" />
                    <figcaption class="px-3 py-2 text-xs text-ink-faint">短期访问地址，链接会过期；请勿转发。</figcaption>
                  </figure>
                  <p v-if="mediaError[assetId]" class="text-xs text-wine" role="alert">{{ mediaError[assetId] }}</p>
                </template>
              </div>
            </div>
          </SurfaceCard>

          <!-- 处理进度 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">处理进度</h2>
            <p v-if="isExceptionalStatus" class="mt-3 rounded-sm bg-wine-soft px-4 py-3 text-sm text-wine">
              这条反馈被标记为「{{ statusLabel }}」，不按常规步骤推进。
              <template v-if="feedback.resolution_note">理由：{{ feedback.resolution_note }}</template>
              <template v-else>具体理由见下方说明；如果认为判断有误，可以在本页提交申诉。</template>
            </p>
            <ol class="mt-4 space-y-3">
              <li v-for="(step, index) in FEEDBACK_STATUS_STEPS" :key="step.value" class="flex items-start gap-3">
                <span
                  class="mt-0.5 flex size-6 shrink-0 items-center justify-center rounded-full border text-xs tabular-nums"
                  :class="
                    index <= currentStepIndex
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line bg-surface-muted text-ink-faint'
                  "
                  aria-hidden="true"
                >
                  {{ index + 1 }}
                </span>
                <div>
                  <p class="text-sm" :class="index <= currentStepIndex ? 'text-ink-soft' : 'text-ink-faint'">
                    {{ step.label }}
                    <span v-if="index === currentStepIndex" class="ml-2 text-xs text-amber-strong">当前状态</span>
                  </p>
                </div>
              </li>
            </ol>

            <p v-if="needsInfo" class="mt-4 rounded-sm bg-amber-soft px-4 py-3 text-xs text-amber-strong">
              工作人员需要补充信息。请在下方补充说明或附件；补充关键新证据可以作为同一条反馈升级奖励，仅补差额。
            </p>

            <div v-if="feedback.resolution_note" class="mt-5">
              <h3 class="text-xs text-ink-faint">处理说明</h3>
              <p class="mt-1 whitespace-pre-wrap text-sm text-ink-muted">{{ feedback.resolution_note }}</p>
            </div>
          </SurfaceCard>

          <!-- 奖励结果 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">奖励结果</h2>
            <div class="mt-3 flex flex-wrap items-center gap-2">
              <StatusBadge
                :tone="
                  feedback.reward_status === 'GRANTED' || feedback.reward_status === 'APPROVED'
                    ? 'jade'
                    : feedback.reward_status === 'NOT_ELIGIBLE' || feedback.reward_status === 'REVOKED'
                      ? 'wine'
                      : 'neutral'
                "
                :label="rewardLabel"
              />
            </div>
            <p v-if="feedback.reward_reason" class="mt-4 whitespace-pre-wrap text-sm text-ink-muted">
              {{ feedback.reward_reason }}
            </p>
            <p v-else class="mt-4 text-sm text-ink-muted">
              审核完成后这里会给出奖励或不奖励的理由与具体数值。提交时只显示“待审核”，不显示确定积分。
            </p>
            <p class="mt-4 text-xs leading-relaxed text-ink-faint">
              {{ FEEDBACK_FAIRNESS_NOTICE }}
            </p>
          </SurfaceCard>

          <!-- 沟通记录 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">沟通记录</h2>
            <EmptyState
              v-if="feedback.messages.length === 0"
              class="mt-3"
              title="还没有补充消息"
              description="需要补充信息或证据时，可以在下方发送；所有消息都留痕。"
            />
            <ul v-else class="mt-4 space-y-4">
              <li v-for="message in feedback.messages" :key="message.id" class="rounded-sm border border-line p-4">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <StatusBadge
                    :tone="message.author === 'staff' ? 'amber' : 'neutral'"
                    :label="message.author === 'staff' ? '工作室' : '我'"
                  />
                  <span class="text-xs tabular-nums text-ink-faint">{{ formatDateTime(message.created_at) }}</span>
                </div>
                <p class="mt-2 whitespace-pre-wrap text-sm text-ink-muted">{{ message.body }}</p>
              </li>
            </ul>

            <div class="mt-5">
              <FieldRow
                label="补充说明"
                for="feedback-message"
                :error="messageError ?? ''"
                hint="补充新的复现步骤、截图或说明。请不要在这里填写身份证件、银行卡或完整住址。"
              >
                <textarea id="feedback-message" v-model="messageDraft" class="field-base min-h-[100px]" maxlength="2000" />
              </FieldRow>
              <div class="mt-3">
                <AppButton size="sm" :loading="messageSaving" @click="sendMessage">发送补充</AppButton>
              </div>
            </div>
          </SurfaceCard>

          <!-- 申诉 -->
          <SurfaceCard>
            <h2 class="font-display text-lg">申诉</h2>
            <p class="mt-3 text-sm text-ink-muted">
              对奖励判定或不采纳的理由有疑问时可以申诉。撤回反馈不会自动删除已发积分，
              只有在复核确认作弊时才会追加冲正。
            </p>

            <ul v-if="feedback.appeals.length" class="mt-4 divide-y divide-line">
              <li v-for="appeal in feedback.appeals" :key="appeal.id" class="py-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <StatusBadge tone="neutral" :label="appeal.status" />
                  <span class="text-xs tabular-nums text-ink-faint">{{ formatDateTime(appeal.created_at) }}</span>
                </div>
                <p class="mt-2 text-sm text-ink-muted">{{ appeal.reason }}</p>
                <p v-if="appeal.resolution_note" class="mt-1 text-xs text-ink-faint">结论：{{ appeal.resolution_note }}</p>
              </li>
            </ul>

            <div v-if="appealOpen" class="mt-4">
              <FieldRow
                label="申诉说明"
                for="feedback-appeal"
                required
                :error="appealError ?? ''"
                hint="写清你看到的结果、你认为应有的结果与依据，至少 10 个字。"
              >
                <textarea id="feedback-appeal" v-model="appealDraft" class="field-base min-h-[110px]" maxlength="2000" />
              </FieldRow>
              <div class="mt-3 flex flex-wrap gap-3">
                <AppButton size="sm" :loading="appealSaving" @click="submitAppeal">提交申诉</AppButton>
                <AppButton size="sm" variant="secondary" @click="appealOpen = false">取消</AppButton>
              </div>
            </div>
            <div v-else class="mt-4">
              <AppButton size="sm" variant="secondary" @click="appealOpen = true">提交申诉</AppButton>
            </div>
          </SurfaceCard>
        </div>

        <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <SurfaceCard>
            <h2 class="font-display text-base">处理时限</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">
              反馈首响为 {{ site.brand.response_sla.feedback_workdays }} 个工作日。
              需要补充信息时会先进入“待补充”，补件后重新计算处理时间。
            </p>
            <p class="mt-3 text-xs text-ink-faint">
              工作时段 {{ site.brand.service_hours }}；也可以直接发邮件到
              <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">隐私提示</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">{{ FEEDBACK_PRIVACY_NOTICE }}</p>
            <p class="mt-3 text-xs text-ink-faint">
              重复反馈只关联原反馈的匿名摘要，不会公开原提交者的身份。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">相关入口</h2>
            <div class="mt-3 flex flex-wrap gap-2">
              <AppButton size="sm" variant="secondary" to="/account/feedback">返回列表</AppButton>
              <AppButton size="sm" variant="secondary" to="/account/points">查看星绒币</AppButton>
              <AppButton size="sm" variant="ghost" to="/feedback/new">提交新反馈</AppButton>
            </div>
          </SurfaceCard>
        </aside>
      </div>
    </template>
  </div>
</template>
