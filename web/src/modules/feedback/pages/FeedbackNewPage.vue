<script setup lang="ts">
/**
 * 提交反馈。
 *
 * 规则源：文档/09（反馈处理、审核标准）、文档/06（反馈输入与响应）、
 * 文档/03（`/feedback/new`：问题/建议/体验反馈、页面路径、复现步骤、附件、隐私提示）。
 *
 * 关键约束：
 * - 分类固定为六项；title 1–100 字、body 10–5000 字、附件最多 3 张。
 * - `page_path` 只填本站路径，并自动去掉 query/hash 中的敏感值。
 * - 提交响应只显示“奖励状态：待审核”，不显示确定积分。
 * - 不要求用户删除差评、给好评或说正面内容，页面写明这一点。
 */
import { computed, onMounted, reactive, ref } from "vue";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  UPLOAD_ACCEPT,
  describeErrorCode,
  runUploadFlow,
  useDocumentTitle,
  validateDesignFile,
} from "@/modules/commissions/api/commissions";
import {
  FEEDBACK_CATEGORIES,
  FEEDBACK_FAIRNESS_NOTICE,
  FEEDBACK_LIMITS,
  FEEDBACK_PRIVACY_NOTICE,
  REWARD_STATUS_LABEL,
  createFeedback,
  currentPagePath,
  hasFeedbackErrors,
  sanitizePagePath,
  validateFeedbackDraft,
  type FeedbackCategory,
  type FeedbackCreateResult,
  type FeedbackFieldErrors,
  type FeedbackInput,
} from "@/modules/feedback/api/feedback";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { PointsIcon, StarLoader } from "@/shared/ui/BrandMark";
import { formatDateTime } from "@/shared/lib/format";
import { ApiError, newIdempotencyKey } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();
const setTitle = useDocumentTitle();

const draft = reactive<FeedbackInput>({
  category: "bug",
  title: "",
  body: "",
  page_path: "",
  steps: "",
  asset_ids: [],
});

const errors = ref<FeedbackFieldErrors>({});
const creating = ref(false);
const createError = ref<string | null>(null);
const createErrorCode = ref("");
const result = ref<FeedbackCreateResult | null>(null);
const idempotencyKey = ref<string | null>(null);
const sessionExpired = ref(false);
const forbidden = ref(false);

interface Attachment {
  id: string;
  filename: string;
  state: "uploading" | "pending" | "scanning" | "ready" | "rejected";
  rejectReason: string | null;
}
const attachments = ref<Attachment[]>([]);
const uploading = ref(false);
const uploadError = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

onMounted(async () => {
  setTitle("提交反馈");
  void site.load();
  // 自动带上当前站点路径（已去掉 query/hash 中的敏感值）。
  draft.page_path = currentPagePath(window.location);
  if (!auth.loaded) {
    await auth.loadMe().catch(() => null);
  }
  if (!auth.isAuthenticated) {
    forbidden.value = true;
  }
});

const readyCount = computed(() => attachments.value.filter((item) => item.state === "ready").length);

const allAttachmentsReady = computed(
  () => attachments.value.every((item) => item.state === "ready"),
);

function fieldError(field: keyof FeedbackInput): string {
  return errors.value[field] ?? "";
}

function onPagePathInput(): void {
  // 输入任何地址都会立即收敛成本站路径；粘贴完整 URL 也不会保存敏感参数。
  draft.page_path = sanitizePagePath(draft.page_path);
}

function onPickFiles(): void {
  fileInput.value?.click();
}

async function onFilesChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = "";
  if (files.length === 0) return;
  uploadError.value = null;

  const slots = FEEDBACK_LIMITS.assets.max - attachments.value.length;
  if (slots <= 0) {
    uploadError.value = "附件最多 3 张，请先移除多余的文件。";
    return;
  }
  const accepted = files.slice(0, slots);
  if (files.length > slots) {
    uploadError.value = `附件最多 3 张，本次只处理前 ${slots} 张。`;
  }

  uploading.value = true;
  try {
    for (const file of accepted) {
      const localError = validateDesignFile(file);
      if (localError) {
        uploadError.value = localError;
        continue;
      }
      const placeholder: Attachment = {
        id: `pending-${file.name}-${file.size}`,
        filename: file.name,
        state: "uploading",
        rejectReason: null,
      };
      attachments.value = [...attachments.value, placeholder];
      try {
        const uploaded = await runUploadFlow(file, { purpose: "design" });
        attachments.value = attachments.value.map((item) =>
          item.id === placeholder.id
            ? {
                id: uploaded.asset_id,
                filename: uploaded.filename,
                state: uploaded.scan_status,
                rejectReason: uploaded.reject_reason,
              }
            : item,
        );
      } catch (caught) {
        attachments.value = attachments.value.filter((item) => item.id !== placeholder.id);
        uploadError.value =
          caught instanceof ApiError
            ? describeErrorCode(caught.code, caught.message)
            : "附件上传失败，请检查网络后重试。";
      }
    }
  } finally {
    uploading.value = false;
    draft.asset_ids = attachments.value.filter((item) => item.state === "ready").map((item) => item.id);
  }
}

function removeAttachment(id: string): void {
  attachments.value = attachments.value.filter((item) => item.id !== id);
  draft.asset_ids = attachments.value.filter((item) => item.state === "ready").map((item) => item.id);
}

async function onSubmit(): Promise<void> {
  if (creating.value || result.value) return;
  createError.value = null;
  createErrorCode.value = "";

  const all = validateFeedbackDraft(draft);
  if (!allAttachmentsReady.value) {
    all.asset_ids = "附件还在处理中，全部通过安全检查后才能提交。";
  }
  if (hasFeedbackErrors(all)) {
    errors.value = all;
    const first = document.querySelector<HTMLElement>("[aria-invalid='true']");
    first?.focus();
    return;
  }
  if (!auth.isAuthenticated) {
    sessionExpired.value = true;
    return;
  }

  creating.value = true;
  try {
    if (!idempotencyKey.value) idempotencyKey.value = newIdempotencyKey();
    result.value = await createFeedback({ ...draft, asset_ids: [...draft.asset_ids] }, idempotencyKey.value);
    toast.success("反馈已提交", "奖励状态会在审核后更新，你可以在“我的反馈”里跟进。");
  } catch (caught) {
    if (caught instanceof ApiError) {
      createErrorCode.value = caught.code;
      if (caught.needsLogin) {
        sessionExpired.value = true;
        createError.value = "登录状态已过期。填写的内容仍保留在本页，请重新登录后重新提交。";
        return;
      }
      if (caught.code === "FEEDBACK_DUPLICATE") {
        createError.value =
          "系统判断这条反馈与已提交的内容高度重复。你可以在“我的反馈”里找到原记录并补充新证据，补充关键新证据可以升级奖励。";
        return;
      }
      if (caught.code === "FEEDBACK_LIMIT_EXCEEDED") {
        createError.value =
          "提交过于频繁（当前限制为每天 10 条、每小时 3 条）。稍后再提交即可；限流只影响提交频率，不代表反馈无效。";
        return;
      }
      createError.value = describeErrorCode(caught.code, caught.message);
      if (Object.keys(caught.fields ?? {}).length > 0) {
        errors.value = { ...errors.value, ...(caught.fields as FeedbackFieldErrors) };
      }
      return;
    }
    createError.value = "提交失败，请检查网络后重试。已填写的内容不会丢失。";
  } finally {
    creating.value = false;
  }
}

const rewardRows = computed(() => [
  { key: "minor", label: "内容纠错或具体可用的建议", points: site.loyaltyRules.feedback_rewards.minor },
  { key: "normal", label: "有效且可复现的功能缺陷", points: site.loyaltyRules.feedback_rewards.normal },
  { key: "major", label: "经确认的严重缺陷或高价值改进", points: site.loyaltyRules.feedback_rewards.major },
]);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '联系与反馈', to: '/contact' },
        { label: '提交反馈' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">Feedback</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">提交反馈</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        功能问题、使用体验、产品建议、内容纠错、售后相关与其他问题都可以提交。
        没有消费记录也可以提交网站问题；涉及具体订单的售后会转到售后单处理。
      </p>
    </header>

    <EmptyState
      v-if="forbidden && !result"
      class="mt-8"
      tone="forbidden"
      title="需要登录后提交反馈"
      description="反馈与奖励记录只对本人开放。登录后会回到这一页，填写内容会重新开始。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <!-- 提交结果：只显示“待审核”，不显示确定积分 -->
    <template v-else-if="result">
      <SurfaceCard class="mt-8">
        <div class="flex flex-wrap items-center gap-3">
          <StatusBadge tone="jade" label="已提交" />
          <StatusBadge tone="neutral" :label="`反馈编号 ${result.id}`" />
        </div>
        <h2 class="mt-4 font-display text-2xl">反馈已提交</h2>
        <dl class="mt-5 grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt class="text-ink-faint">处理状态</dt>
            <dd class="mt-1 text-ink-soft">已提交（{{ result.status }}）</dd>
          </div>
          <div>
            <dt class="text-ink-faint">奖励状态</dt>
            <dd class="mt-1 text-ink-soft">{{ REWARD_STATUS_LABEL[result.reward_status] ?? result.reward_status }}</dd>
          </div>
          <div>
            <dt class="text-ink-faint">提交时间</dt>
            <dd class="mt-1 tabular-nums text-ink-soft">{{ formatDateTime(new Date().toISOString()) }}</dd>
          </div>
          <div>
            <dt class="text-ink-faint">首响时间</dt>
            <dd class="mt-1 text-ink-soft">{{ site.brand.response_sla.feedback_workdays }} 个工作日</dd>
          </div>
        </dl>

        <p class="mt-5 rounded-sm bg-canvas-sunk px-4 py-3 text-xs leading-relaxed text-ink-muted">
          奖励需要人工审核，所以提交时不会显示确定积分。审核完成后，“我的反馈”里会给出奖励或不奖励的理由与具体数值。
          是否奖励只与反馈是否有效、可复现、可改进有关，与你是否给出好评无关。
        </p>

        <div class="mt-6 flex flex-wrap gap-3">
          <AppButton to="/account/feedback">查看我的反馈</AppButton>
          <AppButton variant="secondary" to="/account/points">查看星绒币</AppButton>
          <AppButton variant="ghost" to="/contact">联系客服</AppButton>
        </div>
      </SurfaceCard>
    </template>

    <template v-else>
      <div v-if="sessionExpired" class="mt-6 rounded-lg border border-wine bg-wine-soft p-5 text-sm text-wine" role="alert">
        <p class="font-medium">登录状态已过期</p>
        <p class="mt-2">填写的内容仍保留在本页，重新登录后回到本页即可继续提交。</p>
        <div class="mt-3">
          <AppButton size="sm" variant="secondary" to="/login">重新登录</AppButton>
        </div>
      </div>

      <div class="mt-8 grid gap-6 lg:grid-cols-[1.35fr_0.65fr]">
        <div class="space-y-6">
          <SurfaceCard>
            <h2 class="font-display text-lg">问题分类</h2>
            <fieldset class="mt-4">
              <legend class="sr-only">反馈分类</legend>
              <div class="grid gap-3 sm:grid-cols-2">
                <label
                  v-for="item in FEEDBACK_CATEGORIES"
                  :key="item.value"
                  class="flex cursor-pointer items-start gap-3 rounded-sm border p-4 transition-colors"
                  :class="draft.category === item.value ? 'border-amber bg-amber-soft' : 'border-line bg-surface hover:border-line-strong'"
                >
                  <input
                    v-model="draft.category"
                    class="mt-1"
                    type="radio"
                    name="category"
                    :value="item.value as FeedbackCategory"
                  />
                  <span>
                    <span class="block text-sm font-medium">{{ item.label }}</span>
                    <span class="mt-1 block text-xs text-ink-muted">{{ item.hint }}</span>
                  </span>
                </label>
              </div>
              <p v-if="fieldError('category')" class="mt-2 text-xs font-medium text-wine" role="alert">
                {{ fieldError("category") }}
              </p>
            </fieldset>
            <p class="mt-4 rounded-sm bg-canvas-sunk px-4 py-3 text-xs leading-relaxed text-ink-muted">
              选择“售后相关”时，如果涉及具体订单，我们会把问题转到对应售后单继续处理；
              同时你仍然可以提交网站本身的问题，我们不会因为没有消费记录而拒绝有效反馈。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-lg">反馈内容</h2>
            <div class="mt-4 space-y-6">
              <FieldRow
                label="标题"
                for="feedback-title"
                required
                :error="fieldError('title')"
                :hint="`1–100 字，写清问题或建议的对象。当前 ${draft.title.length} 字。`"
              >
                <input
                  id="feedback-title"
                  v-model="draft.title"
                  class="field-base"
                  type="text"
                  maxlength="100"
                  :aria-invalid="Boolean(fieldError('title'))"
                />
              </FieldRow>

              <FieldRow
                label="内容"
                for="feedback-body"
                required
                :error="fieldError('body')"
                :hint="`10–5000 字。描述期望结果与实际结果；当前 ${draft.body.length} 字。`"
              >
                <textarea
                  id="feedback-body"
                  v-model="draft.body"
                  class="field-base min-h-[200px]"
                  maxlength="5000"
                  :aria-invalid="Boolean(fieldError('body'))"
                />
              </FieldRow>

              <FieldRow
                label="复现步骤（可选）"
                for="feedback-steps"
                :error="fieldError('steps')"
                hint="可复现的缺陷更容易被确认；写清操作顺序、预期与实际结果。最多 2000 字。"
              >
                <textarea id="feedback-steps" v-model="draft.steps" class="field-base min-h-[120px]" maxlength="2000" />
              </FieldRow>

              <FieldRow
                label="页面路径"
                for="feedback-path"
                required
                :error="fieldError('page_path')"
                hint="只保留本站路径，并会自动去掉 query 与 hash 中的敏感值（令牌、验证码、手机号、订单号等）。"
              >
                <input
                  id="feedback-path"
                  v-model="draft.page_path"
                  class="field-base font-mono text-sm"
                  type="text"
                  autocomplete="off"
                  :aria-invalid="Boolean(fieldError('page_path'))"
                  @blur="onPagePathInput"
                />
              </FieldRow>
              <p class="text-xs text-ink-faint">
                保存的路径示例：<span class="font-mono">{{ draft.page_path || "/" }}</span>
              </p>
            </div>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-lg">附件（最多 3 张）</h2>
            <p class="mt-2 text-sm text-ink-muted">
              仅支持 JPEG / PNG / WebP，单张不超过 10MiB。可以上传截图或录屏截图说明问题。
            </p>

            <div class="mt-4">
              <input
                ref="fileInput"
                class="sr-only"
                type="file"
                :accept="UPLOAD_ACCEPT"
                multiple
                @change="onFilesChosen"
              />
              <AppButton
                variant="secondary"
                :loading="uploading"
                :disabled="attachments.length >= FEEDBACK_LIMITS.assets.max"
                :disabled-reason="
                  attachments.length >= FEEDBACK_LIMITS.assets.max ? '已经上传 3 张，需要先移除一张。' : ''
                "
                @click="onPickFiles"
              >
                选择附件
              </AppButton>
              <p v-if="uploadError" class="mt-2 text-xs font-medium text-wine" role="alert">{{ uploadError }}</p>
              <p v-if="fieldError('asset_ids')" class="mt-2 text-xs font-medium text-wine" role="alert">
                {{ fieldError("asset_ids") }}
              </p>
            </div>

            <ul v-if="attachments.length" class="mt-4 divide-y divide-line border-y border-line">
              <li v-for="item in attachments" :key="item.id" class="flex flex-wrap items-center gap-3 py-3">
                <StatusBadge
                  :tone="item.state === 'ready' ? 'jade' : item.state === 'rejected' ? 'wine' : 'amber'"
                  :label="
                    item.state === 'uploading'
                      ? '正在上传'
                      : item.state === 'ready'
                        ? '可提交'
                        : item.state === 'rejected'
                          ? '未通过检查'
                          : '安全扫描中'
                  "
                />
                <span class="min-w-0 flex-1 truncate text-sm text-ink-soft">{{ item.filename }}</span>
                <AppButton size="sm" variant="quiet" @click="removeAttachment(item.id)">移除</AppButton>
              </li>
            </ul>
            <p v-if="attachments.some((item) => item.rejectReason)" class="mt-3 text-xs text-wine">
              {{ attachments.find((item) => item.rejectReason)?.rejectReason }}
            </p>
            <p class="mt-3 text-xs text-ink-faint">已通过安全检查 {{ readyCount }} / 3。</p>
          </SurfaceCard>

          <div v-if="createError" class="rounded-lg border border-wine bg-wine-soft p-5 text-sm text-wine" role="alert">
            <p class="font-medium">提交未完成</p>
            <p class="mt-2">{{ createError }}</p>
            <p v-if="createErrorCode" class="mt-2 text-xs">错误码：{{ createErrorCode }}</p>
            <div class="mt-4 flex flex-wrap gap-3">
              <AppButton size="sm" variant="secondary" :loading="creating" @click="onSubmit">重试提交</AppButton>
              <AppButton size="sm" variant="ghost" to="/account/feedback">查看我的反馈</AppButton>
            </div>
          </div>

          <div class="flex flex-wrap items-center gap-3">
            <AppButton :loading="creating" @click="onSubmit">提交反馈</AppButton>
            <AppButton variant="secondary" to="/account/feedback">我的反馈</AppButton>
          </div>
        </div>

        <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <SurfaceCard>
            <h2 class="font-display text-base">隐私提示</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">{{ FEEDBACK_PRIVACY_NOTICE }}</p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">关于奖励</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">{{ FEEDBACK_FAIRNESS_NOTICE }}</p>
            <ul class="mt-4 space-y-3 text-sm">
              <li v-for="row in rewardRows" :key="row.key" class="flex flex-wrap items-baseline justify-between gap-3">
                <span class="text-xs text-ink-muted">{{ row.label }}</span>
                <span class="shrink-0">
                  <PointsIcon :size="18" :points="row.points" />
                </span>
              </li>
            </ul>
            <p class="mt-4 text-xs text-ink-faint">
              以上是审核参考等级；提交时不会显示确定积分，只显示“奖励状态：待审核”。
              每天最多奖励 {{ site.loyaltyRules.feedback_daily_reward_limit }} 条（预算草案），
              达到上限后仍可正常提交反馈。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">提交频率</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">
              当前限制为每天 10 条、每小时 3 条，并按风险限流。达到限制时会明确提示，稍后再提交即可。
              我们不会因为同一网络下的其他人提交过内容就限制你的账号。
            </p>
          </SurfaceCard>
        </aside>
      </div>
    </template>

    <div v-if="creating" class="mt-6">
      <StarLoader :size="72" inline message="正在提交反馈" />
    </div>
  </div>
</template>
