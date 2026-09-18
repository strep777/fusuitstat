<script setup lang="ts">
/**
 * 制作进度。
 *
 * 规则源：文档/03（`/account/commissions/:id`：制作时间线、量体资料、阶段确认、变更请求）、
 * 文档/07（制作与变更、失败与恢复）、文档/11（订单信息顺序）。
 *
 * 关键约束：
 * - 时间线展示负责人、开始时间、预计日期、说明、私密图片、需客户确认标记、确认版本与确认时间。
 * - VERSION_CONFLICT 时提示“阶段已更新，请查看最新版本”，不覆盖服务端记录。
 * - 重大加项由变更单表达，写清新金额、延期与条款版本；接受后生成单独补款。
 * - 量体单位：高度/围度 cm、重量 kg，表单显示单位并支持小数；超范围只提示，不排除用户。
 */
import { computed, onMounted, reactive, ref } from "vue";
import { useRoute } from "vue-router";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  MEASUREMENT_FIELDS,
  confirmProductionStage,
  decideChangeRequest,
  describeErrorCode,
  fetchCommissionProject,
  formatMeasurement,
  measurementHint,
  requestProductionChange,
  submitMeasurements,
  useDocumentTitle,
  type CommissionProject,
  type ProductionStage,
} from "@/modules/commissions/api/commissions";
import StageTimeline from "@/modules/commissions/components/StageTimeline.vue";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { StarLoader } from "@/shared/ui/BrandMark";
import { formatDateTime, formatMoneyExact, isExpired } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();
const setTitle = useDocumentTitle();

const projectId = computed(() => String(route.params.id ?? ""));

const project = ref<CommissionProject | null>(null);
const loading = ref(true);
const error = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);

const conflictStageId = ref<string | null>(null);
const busyStageId = ref<string | null>(null);
const actionError = ref<string | null>(null);
const actionErrorCode = ref("");

const measurementForm = reactive<Record<string, string>>({});
const measurementErrors = reactive<Record<string, string>>({});
const measurementSaving = ref(false);
const measurementSaved = ref(false);
const measurementError = ref<string | null>(null);

const decidingChangeId = ref<string | null>(null);

async function load(): Promise<void> {
  loading.value = true;
  error.value = null;
  forbidden.value = false;
  notFound.value = false;
  try {
    const detail = await fetchCommissionProject(projectId.value);
    project.value = detail;
    setTitle(detail.title ? `制作进度 · ${detail.title}` : "制作进度");
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin || caught.status === 403) forbidden.value = true;
      else if (caught.status === 404) notFound.value = true;
      else error.value = caught.message;
    } else {
      error.value = "制作信息加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

onMounted(async () => {
  setTitle("制作进度");
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

/* ---------------- 阶段确认 / 修改请求 ---------------- */

async function onConfirm(payload: { stageId: string; version: number; note: string }): Promise<void> {
  if (busyStageId.value) return;
  busyStageId.value = payload.stageId;
  actionError.value = null;
  actionErrorCode.value = "";
  conflictStageId.value = null;
  try {
    const updated = await confirmProductionStage(payload.stageId, {
      version: payload.version,
      confirmed: true,
      note: payload.note || undefined,
    });
    applyStageUpdate(updated);
    toast.success("阶段已确认", "确认时间与版本会记录在案。");
  } catch (caught) {
    handleStageError(caught, payload.stageId);
  } finally {
    busyStageId.value = null;
  }
}

async function onRequestChange(payload: { stageId: string; version: number; description: string }): Promise<void> {
  if (busyStageId.value) return;
  busyStageId.value = payload.stageId;
  actionError.value = null;
  actionErrorCode.value = "";
  conflictStageId.value = null;
  try {
    const change = await requestProductionChange(payload.stageId, {
      version: payload.version,
      description: payload.description,
    });
    toast.info(
      "修改请求已提交",
      change.additional_cents
        ? "这项变更涉及金额，工作人员会先发布变更单，你接受后才执行。"
        : "工作人员会按你的说明评估是否需要新的变更单。",
    );
    await load();
  } catch (caught) {
    handleStageError(caught, payload.stageId);
  } finally {
    busyStageId.value = null;
  }
}

function applyStageUpdate(updated: ProductionStage): void {
  if (!project.value) return;
  project.value = {
    ...project.value,
    stages: project.value.stages.map((stage) => (stage.id === updated.id ? { ...stage, ...updated } : stage)),
  };
}

function handleStageError(caught: unknown, stageId: string): void {
  if (caught instanceof ApiError) {
    actionErrorCode.value = caught.code;
    if (caught.needsLogin) {
      actionError.value = "登录状态已过期，请重新登录后再操作。";
      return;
    }
    if (caught.isVersionConflict) {
      conflictStageId.value = stageId;
      actionError.value = "阶段已更新，请查看最新版本后再确认或提出修改。";
      return;
    }
    actionError.value = describeErrorCode(caught.code, caught.message);
    return;
  }
  actionError.value = "操作失败，请检查网络后重试。本次操作没有写入任何记录。";
}

/* ---------------- 变更单决策 ---------------- */

async function decideChange(changeId: string, accepted: boolean, version: number): Promise<void> {
  if (decidingChangeId.value) return;
  decidingChangeId.value = changeId;
  actionError.value = null;
  actionErrorCode.value = "";
  try {
    const updated = await decideChangeRequest(changeId, { accepted, version });
    if (project.value) {
      project.value = {
        ...project.value,
        change_requests: project.value.change_requests.map((item) =>
          item.id === changeId ? { ...item, ...updated } : item,
        ),
      };
    }
    toast.success(
      accepted ? "变更单已接受" : "变更单已拒绝",
      accepted
        ? "加项会生成单独的补款，已有付款与旧条款快照不会被改写。"
        : "工作室会按原方案继续；如有疑问可以直接联系客服。",
    );
  } catch (caught) {
    if (caught instanceof ApiError) {
      actionErrorCode.value = caught.code;
      actionError.value = caught.isVersionConflict
        ? "变更单已更新，请刷新后查看最新版本再决定。"
        : describeErrorCode(caught.code, caught.message);
    } else {
      actionError.value = "提交失败，请检查网络后重试。";
    }
  } finally {
    decidingChangeId.value = null;
  }
}

/* ---------------- 量体 ---------------- */

const latestMeasurement = computed(() => {
  const list = project.value?.measurements ?? [];
  if (list.length === 0) return null;
  return list.reduce((best, item) => (item.version > best.version ? item : best), list[0]!);
});

const measurementValues = computed<Record<string, string>>(() => {
  const map: Record<string, string> = {};
  for (const item of latestMeasurement.value?.items ?? []) {
    map[item.kind] = Number.isInteger(item.value) ? String(item.value) : item.value.toFixed(1);
  }
  return map;
});

/** 单位与提示：只做异常范围提示，特殊体型由工作人员确认，不硬编码排除用户。 */
function hintFor(kind: string, unit: string): string {
  const raw = measurementForm[kind] ?? measurementValues.value[kind] ?? "";
  const value = Number(raw);
  if (!raw || Number.isNaN(value)) return "";
  return measurementHint(kind, value, unit);
}

async function saveMeasurements(): Promise<void> {
  if (!project.value || measurementSaving.value) return;
  const items: Array<{ kind: string; value: number; unit: string; note: string | null }> = [];
  let hasError = false;
  Object.keys(measurementErrors).forEach((key) => delete measurementErrors[key]);

  for (const field of MEASUREMENT_FIELDS) {
    const raw = (measurementForm[field.kind] ?? "").trim();
    if (!raw) continue;
    const value = Number(raw);
    if (Number.isNaN(value) || value <= 0) {
      measurementErrors[field.kind] = "请填写大于 0 的数字，可以带一位小数。";
      hasError = true;
      continue;
    }
    if (!/^\d+(\.\d{1,2})?$/.test(raw)) {
      measurementErrors[field.kind] = "最多两位小数，例如 57.5。";
      hasError = true;
      continue;
    }
    items.push({ kind: field.kind, value, unit: field.unit, note: null });
  }
  if (hasError) return;
  if (items.length === 0) {
    measurementError.value = "至少填写一项量体数据；不确定的项目可以先留空，工作人员会另行确认。";
    return;
  }

  measurementSaving.value = true;
  measurementError.value = null;
  measurementSaved.value = false;
  try {
    await submitMeasurements(project.value.id, { version: project.value.version, items });
    measurementSaved.value = true;
    toast.success("量体数据已提交", "会生成一个新的量体版本，历史版本仍然可查。");
    await load();
  } catch (caught) {
    if (caught instanceof ApiError && caught.isVersionConflict) {
      measurementError.value = "制作单已更新，请刷新后重新提交，避免覆盖已有量体版本。";
    } else if (caught instanceof ApiError) {
      measurementError.value = describeErrorCode(caught.code, caught.message);
    } else {
      measurementError.value = "提交失败，请检查网络后重试；已填写的数字会保留。";
    }
  } finally {
    measurementSaving.value = false;
  }
}

/* ---------------- 展示辅助 ---------------- */

const pendingStages = computed(
  () => project.value?.stages.filter((stage) => stage.requires_customer_confirm && !stage.confirmed_at) ?? [],
);

const pendingChanges = computed(
  () => project.value?.change_requests.filter((item) => item.status === "PENDING") ?? [],
);

const balanceOverdue = computed(
  () => Boolean(project.value?.balance_due_at) && isExpired(project.value?.balance_due_at ?? null),
);

const confirmBlockedReason = computed(() => {
  if (balanceOverdue.value) return "尾款已到期，需先完成尾款核对后才能确认后续阶段。";
  return "";
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '我的账户', to: '/account' },
        { label: '制作进度' },
      ]"
    />

    <div v-if="loading" class="mt-8">
      <StarLoader message="正在读取制作进度" hint="包含阶段记录与量体资料" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="需要登录后查看"
      description="制作信息只对本人和分配的工作人员开放。登录后会回到这一页。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/account/orders">查看我的订单</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="notFound"
      class="mt-8"
      tone="not-found"
      title="没有找到这个制作单"
      description="它可能属于其他账号，或者编号不正确。可以从订单页进入对应的制作进度。"
    >
      <AppButton size="sm" to="/account/orders">返回订单列表</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="制作信息加载失败"
      :description="`${error} 可以重试；制作记录不会因为打不开页面而丢失。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="project">
      <header class="mt-6 max-w-3xl">
        <div class="flex flex-wrap items-center gap-2">
          <StatusBadge tone="amber" :label="project.status" />
          <StatusBadge tone="neutral" :label="`制作单 ${project.id}`" />
          <StatusBadge v-if="pendingChanges.length" tone="wine" :label="`${pendingChanges.length} 份变更单待决定`" />
        </div>
        <h1 class="mt-4 font-display text-3xl leading-tight">{{ project.title }}</h1>
        <p class="mt-3 text-sm text-ink-muted">
          {{ project.series_title || "未关联系列" }} · {{ project.suit_type }} · 条款版本
          <span class="font-mono">{{ project.terms_version }}</span>
        </p>
      </header>

      <!-- 当前需要的动作 -->
      <SurfaceCard class="mt-8">
        <h2 class="font-display text-lg">当前需要的动作</h2>
        <ul class="mt-4 space-y-2 text-sm text-ink-muted">
          <li v-if="pendingStages.length">
            有 {{ pendingStages.length }} 个阶段等待你确认：{{
              pendingStages.map((stage) => stage.title).join("、")
            }}。
          </li>
          <li v-if="pendingChanges.length">
            有 {{ pendingChanges.length }} 份变更单待决定，涉及金额或延期，需要你明确接受或拒绝。
          </li>
          <li v-if="balanceOverdue" class="text-wine">
            尾款已在 {{ formatDateTime(project.balance_due_at) }} 到期。请先完成尾款核对，
            发货前会做尾款校验。
          </li>
          <li v-if="!pendingStages.length && !pendingChanges.length && !balanceOverdue">
            现在没有需要你处理的事项。新的阶段或变更单出现时会在这里提示，并同步到通知。
          </li>
        </ul>
      </SurfaceCard>

      <div v-if="actionError" class="mt-6 rounded-lg border border-wine bg-wine-soft p-5 text-sm text-wine" role="alert">
        <p class="font-medium">本次操作没有完成</p>
        <p class="mt-2">{{ actionError }}</p>
        <p v-if="actionErrorCode" class="mt-2 text-xs">错误码：{{ actionErrorCode }}</p>
        <div class="mt-4 flex flex-wrap gap-3">
          <AppButton size="sm" variant="secondary" @click="load()">刷新最新版本</AppButton>
          <AppButton size="sm" variant="ghost" to="/contact">联系客服</AppButton>
        </div>
      </div>

      <div class="mt-8 grid gap-6 lg:grid-cols-[1.35fr_0.65fr]">
        <div class="space-y-6">
          <!-- 时间线 -->
          <section>
            <div class="mb-4 flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-xl">制作时间线</h2>
              <StatusBadge tone="neutral" :label="`共 ${project.stages.length} 个阶段`" />
            </div>
            <StageTimeline
              :stages="project.stages"
              :actionable="!balanceOverdue"
              :blocked-reason="confirmBlockedReason"
              :conflict-stage-id="conflictStageId"
              :busy-stage-id="busyStageId"
              @confirm="onConfirm"
              @request-change="onRequestChange"
              @reload="load()"
            />
          </section>

          <!-- 变更单 -->
          <section v-if="project.change_requests.length">
            <h2 class="mb-4 font-display text-xl">变更单</h2>
            <div class="space-y-4">
              <SurfaceCard v-for="change in project.change_requests" :key="change.id">
                <div class="flex flex-wrap items-start justify-between gap-3">
                  <div>
                    <p class="eyebrow">{{ change.id }}</p>
                    <p class="mt-2 text-sm text-ink-soft">{{ change.description }}</p>
                  </div>
                  <StatusBadge
                    :tone="
                      change.status === 'ACCEPTED'
                        ? 'jade'
                        : change.status === 'PENDING'
                          ? 'amber'
                          : 'neutral'
                    "
                    :label="
                      change.status === 'ACCEPTED'
                        ? '已接受'
                        : change.status === 'PENDING'
                          ? '待你决定'
                          : change.status === 'DECLINED'
                            ? '已拒绝'
                            : change.status
                    "
                  />
                </div>
                <dl class="mt-4 grid gap-3 text-sm sm:grid-cols-3">
                  <div>
                    <dt class="text-xs text-ink-faint">新增金额</dt>
                    <dd class="mt-0.5 tabular-nums text-ink-soft">
                      {{ change.additional_cents === null ? "无" : formatMoneyExact(change.additional_cents) }}
                    </dd>
                  </div>
                  <div>
                    <dt class="text-xs text-ink-faint">延期</dt>
                    <dd class="mt-0.5 tabular-nums text-ink-soft">
                      {{ change.delay_days === null ? "无" : `${change.delay_days} 天` }}
                    </dd>
                  </div>
                  <div>
                    <dt class="text-xs text-ink-faint">条款版本</dt>
                    <dd class="mt-0.5 font-mono text-ink-soft">{{ change.terms_version }}</dd>
                  </div>
                </dl>
                <p class="mt-3 text-xs text-ink-faint">
                  提出于 {{ formatDateTime(change.created_at) }}
                  <template v-if="change.decided_at">· 决定于 {{ formatDateTime(change.decided_at) }}</template>
                </p>
                <div v-if="change.status === 'PENDING'" class="mt-4 flex flex-wrap gap-3">
                  <AppButton
                    size="sm"
                    :loading="decidingChangeId === change.id"
                    @click="decideChange(change.id, true, change.version ?? project.version)"
                  >
                    接受变更单
                  </AppButton>
                  <AppButton
                    size="sm"
                    variant="secondary"
                    :loading="decidingChangeId === change.id"
                    @click="decideChange(change.id, false, change.version ?? project.version)"
                  >
                    拒绝变更单
                  </AppButton>
                </div>
                <p v-else-if="change.status === 'ACCEPTED'" class="mt-3 text-xs text-ink-muted">
                  接受后产生单独补款，已经成功的付款记录与旧条款快照保持不变。
                </p>
              </SurfaceCard>
            </div>
          </section>
        </div>

        <!-- 侧栏 -->
        <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <SurfaceCard>
            <h2 class="font-display text-base">时间安排</h2>
            <dl class="mt-3 space-y-2 text-xs">
              <div class="flex justify-between gap-3">
                <dt class="text-ink-faint">建立时间</dt>
                <dd class="tabular-nums text-ink-soft">{{ formatDateTime(project.created_at) }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt class="text-ink-faint">预计开始</dt>
                <dd class="tabular-nums text-ink-soft">{{ formatDateTime(project.estimated_start) }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt class="text-ink-faint">预计交付</dt>
                <dd class="tabular-nums text-ink-soft">{{ formatDateTime(project.estimated_delivery) }}</dd>
              </div>
              <div class="flex justify-between gap-3">
                <dt class="text-ink-faint">尾款期限</dt>
                <dd class="tabular-nums text-ink-soft">{{ formatDateTime(project.balance_due_at) }}</dd>
              </div>
            </dl>
            <p class="mt-3 text-xs text-ink-faint">
              预计时间是计划值；工作室原因的延期会更新预计时间并说明原因。等待你回复造成的延期也会留下记录。
            </p>
          </SurfaceCard>

          <!-- 量体资料 -->
          <SurfaceCard>
            <div class="flex flex-wrap items-center justify-between gap-3">
              <h2 class="font-display text-base">量体资料</h2>
              <StatusBadge
                v-if="latestMeasurement"
                tone="neutral"
                :label="`当前版本 v${latestMeasurement.version}`"
              />
            </div>

            <p v-if="latestMeasurement" class="mt-3 text-xs text-ink-faint">
              最近提交 {{ formatDateTime(latestMeasurement.created_at) }}。历史版本仍可查，不会覆盖。
            </p>

            <ul v-if="latestMeasurement" class="mt-3 space-y-1.5 text-xs">
              <li v-for="item in latestMeasurement.items" :key="item.kind" class="flex justify-between gap-3">
                <span class="text-ink-faint">
                  {{ MEASUREMENT_FIELDS.find((field) => field.kind === item.kind)?.label ?? item.kind }}
                </span>
                <span class="tabular-nums text-ink-soft">{{ formatMeasurement(item.value, item.unit) }}</span>
              </li>
            </ul>
            <p v-else class="mt-3 text-xs text-ink-muted">
              还没有量体记录。按下面的项目在家测量后提交即可，单位与小数位在说明里给出。
            </p>

            <details class="mt-4 rounded-sm border border-line px-4 py-3">
              <summary class="cursor-pointer text-sm font-medium text-ink-soft">
                填写 / 更新量体数据
              </summary>
              <div class="mt-4 grid gap-4 sm:grid-cols-2">
                <FieldRow
                  v-for="field in MEASUREMENT_FIELDS"
                  :key="field.kind"
                  :label="`${field.label}（${field.unit}）`"
                  :for="`measure-${field.kind}`"
                  :error="measurementErrors[field.kind] ?? ''"
                  :hint="hintFor(field.kind, field.unit) || field.hint"
                >
                  <input
                    :id="`measure-${field.kind}`"
                    v-model="measurementForm[field.kind]"
                    class="field-base tabular-nums"
                    type="text"
                    inputmode="decimal"
                    :placeholder="measurementValues[field.kind] ?? ''"
                    autocomplete="off"
                  />
                </FieldRow>
              </div>
              <p v-if="measurementError" class="mt-3 text-xs font-medium text-wine" role="alert">
                {{ measurementError }}
              </p>
              <p v-if="measurementSaved" class="mt-3 text-xs text-jade" role="status">
                量体数据已提交，制作单会按新版本继续。
              </p>
              <div class="mt-4 flex flex-wrap gap-3">
                <AppButton size="sm" :loading="measurementSaving" @click="saveMeasurements">提交量体数据</AppButton>
              </div>
              <p class="mt-3 text-xs text-ink-faint">
                量体数据按高敏感度内部资料保护，只用于本单版型与结构。超出常见范围时会提示复核，
                特殊体型由工作人员单独确认。
              </p>
            </details>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">规则与帮助</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">
              免费修改 {{ site.commissionPolicy.free_revision_count }} 次；重大加项会先生成变更单。
              确认表示你已核对本阶段结果，有疑问请先提出修改。
            </p>
            <div class="mt-3 flex flex-wrap gap-2">
              <AppButton size="sm" variant="secondary" to="/commission/contract">条款说明</AppButton>
              <AppButton size="sm" variant="ghost" to="/contact">联系客服</AppButton>
            </div>
          </SurfaceCard>
        </aside>
      </div>
    </template>
  </div>
</template>
