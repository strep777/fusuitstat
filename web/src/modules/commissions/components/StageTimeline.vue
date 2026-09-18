<script setup lang="ts">
/**
 * 制作阶段时间线。
 *
 * 规则源：文档/07（制作与变更）、文档/11（订单信息顺序）。
 *
 * 每个阶段显示：负责人、开始时间、预计日期、说明、私密图片、需客户确认标记、
 * 确认版本与确认时间。客户可以确认或提出修改；工作人员不能代替客户确认，
 * 因此这里的所有记录都标注了来源（客户确认 / 等待客户回复 / 工作室计划）。
 */
import { ref } from "vue";

import {
  fetchMediaAccess,
  type ProductionStage,
} from "@/modules/commissions/api/commissions";
import { AppButton, EmptyState, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

// 这些 props 只在模板中使用；不要赋给局部变量，否则 vue-tsc 会报未使用。
defineProps<{
  stages: ProductionStage[];
  /** 阶段列表整体是否可确认（例如订单未付款时不可确认）。 */
  actionable?: boolean;
  /** 不可确认的原因，必须显示在按钮旁。 */
  blockedReason?: string;
  /** 发生 VERSION_CONFLICT 的阶段 id：提示查看最新版本，不覆盖服务端记录。 */
  conflictStageId?: string | null;
  /** 正在提交的阶段 id。 */
  busyStageId?: string | null;
}>();

const emit = defineEmits<{
  (event: "confirm", payload: { stageId: string; version: number; note: string }): void;
  (event: "request-change", payload: { stageId: string; version: number; description: string }): void;
  (event: "reload"): void;
}>();

const openChangeFor = ref<string | null>(null);
const changeDraft = ref("");
const changeError = ref<string | null>(null);
const confirmNote = ref<Record<string, string>>({});

const mediaUrls = ref<Record<string, string>>({});
const mediaLoading = ref<string | null>(null);
const mediaError = ref<Record<string, string>>({});

const STATUS_META: Record<string, { label: string; tone: "neutral" | "amber" | "jade" | "wine" | "night" }> = {
  PENDING: { label: "未开始", tone: "neutral" },
  IN_PROGRESS: { label: "进行中", tone: "amber" },
  DONE: { label: "已完成", tone: "jade" },
  BLOCKED: { label: "阻塞中", tone: "wine" },
};

function statusMeta(status: string) {
  return STATUS_META[status] ?? { label: status || "状态未知", tone: "neutral" as const };
}

/** 私密图片通过短期访问地址加载，不把长期地址写进页面或本地存储。 */
async function toggleMedia(assetId: string): Promise<void> {
  if (mediaUrls.value[assetId]) {
    const next = { ...mediaUrls.value };
    delete next[assetId];
    mediaUrls.value = next;
    return;
  }
  mediaLoading.value = assetId;
  const nextError = { ...mediaError.value };
  delete nextError[assetId];
  mediaError.value = nextError;
  try {
    const access = await fetchMediaAccess(assetId);
    mediaUrls.value = { ...mediaUrls.value, [assetId]: access.url };
  } catch (caught) {
    mediaError.value = {
      ...mediaError.value,
      [assetId]:
        caught instanceof ApiError
          ? `无法获取访问地址：${caught.message}`
          : "无法获取访问地址，请稍后重试。",
    };
  } finally {
    mediaLoading.value = null;
  }
}

function startChange(stageId: string): void {
  openChangeFor.value = stageId;
  changeDraft.value = "";
  changeError.value = null;
}

function cancelChange(): void {
  openChangeFor.value = null;
  changeDraft.value = "";
  changeError.value = null;
}

function submitChange(stage: ProductionStage): void {
  const text = changeDraft.value.trim();
  if (text.length < 5) {
    changeError.value = "请写清楚需要修改的地方，至少 5 个字。";
    return;
  }
  // 版本用于并发校验；服务端记录里没有版本时不伪造一个已确认版本，交由服务端判定。
  emit("request-change", { stageId: stage.id, version: stage.confirm_version ?? 0, description: text });
  cancelChange();
}

function submitConfirm(stage: ProductionStage): void {
  emit("confirm", {
    stageId: stage.id,
    version: stage.confirm_version ?? 0,
    note: (confirmNote.value[stage.id] ?? "").trim(),
  });
}
</script>

<template>
  <div>
    <EmptyState
      v-if="stages.length === 0"
      title="还没有制作阶段记录"
      description="进入制作队列后，每个阶段都会在这里登记负责人、时间与说明。没有记录时不会用推测的进度填充。"
    />

    <ol v-else class="space-y-5">
      <li v-for="(stage, index) in stages" :key="stage.id">
        <SurfaceCard>
          <div class="flex flex-wrap items-start justify-between gap-3">
            <div class="flex items-start gap-3">
              <span
                class="mt-0.5 flex size-8 shrink-0 items-center justify-center rounded-full border text-xs font-medium tabular-nums"
                :class="
                  stage.status === 'DONE'
                    ? 'border-jade bg-jade-soft text-jade'
                    : stage.status === 'IN_PROGRESS'
                      ? 'border-amber bg-amber-soft text-amber-strong'
                      : 'border-line bg-surface-muted text-ink-faint'
                "
                aria-hidden="true"
              >
                {{ index + 1 }}
              </span>
              <div>
                <h3 class="font-display text-lg">{{ stage.title }}</h3>
                <p class="mt-1 text-xs text-ink-faint">{{ stage.code }}</p>
              </div>
            </div>
            <div class="flex flex-wrap items-center gap-2">
              <StatusBadge :tone="statusMeta(stage.status).tone" :label="statusMeta(stage.status).label" />
              <StatusBadge
                v-if="stage.requires_customer_confirm"
                :tone="stage.confirmed_at ? 'jade' : 'amber'"
                :label="stage.confirmed_at ? '客户已确认' : '需要你确认'"
              />
            </div>
          </div>

          <dl class="mt-4 grid gap-3 text-sm sm:grid-cols-2">
            <div>
              <dt class="text-xs text-ink-faint">负责人</dt>
              <dd class="mt-0.5 text-ink-soft">{{ stage.owner_name || "未指派" }}</dd>
            </div>
            <div>
              <dt class="text-xs text-ink-faint">开始时间</dt>
              <dd class="mt-0.5 tabular-nums text-ink-soft">
                {{ stage.started_at ? formatDateTime(stage.started_at) : "尚未开始" }}
              </dd>
            </div>
            <div>
              <dt class="text-xs text-ink-faint">预计日期</dt>
              <dd class="mt-0.5 tabular-nums text-ink-soft">{{ formatDateTime(stage.estimated_at) }}</dd>
            </div>
            <div>
              <dt class="text-xs text-ink-faint">完成时间</dt>
              <dd class="mt-0.5 tabular-nums text-ink-soft">
                {{ stage.finished_at ? formatDateTime(stage.finished_at) : "未完成" }}
              </dd>
            </div>
          </dl>

          <p v-if="stage.description" class="mt-4 whitespace-pre-wrap text-sm leading-relaxed text-ink-muted">
            {{ stage.description }}
          </p>

          <p v-if="stage.waiting_customer_since" class="mt-3 rounded-sm bg-amber-soft px-3 py-2 text-xs text-amber-strong">
            自 {{ formatDateTime(stage.waiting_customer_since) }} 起等待你的回复；
            这段等待时间会作为延期原因记录在案。
          </p>

          <div v-if="stage.confirmed_at" class="mt-3 rounded-sm bg-jade-soft px-3 py-2 text-xs text-jade">
            客户确认时间 {{ formatDateTime(stage.confirmed_at) }}，确认版本
            {{ stage.confirm_version ?? "—" }}。
          </div>

          <!-- 私密图片：按需换取短期地址 -->
          <div v-if="stage.private_asset_ids.length" class="mt-4">
            <p class="text-xs text-ink-faint">
              私密阶段图片 {{ stage.private_asset_ids.length }} 张，仅你和被分配的工作人员可见。
            </p>
            <div class="mt-2 flex flex-wrap gap-2">
              <AppButton
                v-for="assetId in stage.private_asset_ids"
                :key="assetId"
                size="sm"
                variant="secondary"
                :loading="mediaLoading === assetId"
                @click="toggleMedia(assetId)"
              >
                {{ mediaUrls[assetId] ? "收起图片" : "查看图片" }}
              </AppButton>
            </div>
            <div class="mt-3 space-y-3">
              <template v-for="assetId in stage.private_asset_ids" :key="assetId">
                <figure v-if="mediaUrls[assetId]" class="overflow-hidden rounded-sm border border-line">
                  <img
                    :src="mediaUrls[assetId]"
                    alt="制作阶段私密照片"
                    loading="lazy"
                    decoding="async"
                    class="w-full object-cover"
                  />
                  <figcaption class="px-3 py-2 text-xs text-ink-faint">
                    短期访问地址，链接会过期；请勿转发。
                  </figcaption>
                </figure>
                <p v-if="mediaError[assetId]" class="text-xs text-wine" role="alert">
                  {{ mediaError[assetId] }}
                </p>
              </template>
            </div>
          </div>

          <!-- 版本冲突：提示查看最新版本 -->
          <div
            v-if="conflictStageId === stage.id"
            class="mt-4 rounded-lg border border-wine bg-wine-soft p-4 text-sm text-wine"
            role="alert"
          >
            <p class="font-medium">阶段已更新，请查看最新版本</p>
            <p class="mt-2 text-xs">
              你没看到最新记录，为避免覆盖工作人员已提交的内容，本次操作没有写入。刷新后再决定确认或提出修改。
            </p>
            <div class="mt-3">
              <AppButton size="sm" variant="secondary" @click="emit('reload')">刷新阶段记录</AppButton>
            </div>
          </div>

          <!-- 需要客户确认 -->
          <div v-else-if="stage.requires_customer_confirm && !stage.confirmed_at" class="mt-4 border-t border-line pt-4">
            <FieldRow
              :label="`确认说明（可选，阶段版本 ${stage.confirm_version ?? '—'}）`"
              :for="`confirm-note-${stage.id}`"
              hint="确认表示你已核对本阶段结果；有疑问请先提出修改，不要直接确认。"
            >
              <textarea
                :id="`confirm-note-${stage.id}`"
                v-model="confirmNote[stage.id]"
                class="field-base min-h-[72px]"
                maxlength="500"
              />
            </FieldRow>
            <div class="mt-3 flex flex-wrap items-center gap-3">
              <AppButton
                :loading="busyStageId === stage.id"
                :disabled="actionable === false"
                :disabled-reason="actionable === false ? blockedReason ?? '当前阶段不能确认。' : ''"
                @click="submitConfirm(stage)"
              >
                确认本阶段
              </AppButton>
              <AppButton
                variant="secondary"
                :disabled="actionable === false"
                :disabled-reason="actionable === false ? blockedReason ?? '当前阶段不能提出修改。' : ''"
                @click="startChange(stage.id)"
              >
                提出修改
              </AppButton>
            </div>

            <div v-if="openChangeFor === stage.id" class="mt-4 rounded-sm border border-line p-4">
              <FieldRow
                :label="`需要修改的地方（阶段版本 ${stage.confirm_version ?? '—'}）`"
                :for="`change-${stage.id}`"
                required
                :error="changeError ?? ''"
                hint="写清位置与期望效果。重大加项会先生成变更单，写清新金额、延期与条款版本。"
              >
                <textarea
                  :id="`change-${stage.id}`"
                  v-model="changeDraft"
                  class="field-base min-h-[96px]"
                  maxlength="2000"
                />
              </FieldRow>
              <div class="mt-3 flex flex-wrap gap-3">
                <AppButton size="sm" @click="submitChange(stage)">提交修改请求</AppButton>
                <AppButton size="sm" variant="secondary" @click="cancelChange">取消</AppButton>
              </div>
            </div>
          </div>
        </SurfaceCard>
      </li>
    </ol>
  </div>
</template>
