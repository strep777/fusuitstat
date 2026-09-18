<script setup lang="ts">
/**
 * 星绒币。
 *
 * 规则源：文档/09（账本与显示、购买奖励、若启用兑换或抵扣）、
 * 文档/11（积分页面信息顺序：可用与待入账 → 获得方式 → 流水 → 规则/申诉）。
 *
 * 关键约束：
 * - 每个图标同时显示数值与“星绒币”文字单位，读屏只读一次（PointsIcon 已处理）。
 * - 明确写出：不支持充值、提现、转账，也不能用星绒币替代现金退款。
 * - 账面可能为负（退款扣回），页面说明原因并给出申诉入口。
 * - 规则文案从 site.loyaltyRules 读取，不写死数值。
 * - 兑换未启用时不宣传未上线权益。
 */
import { computed, onMounted, ref } from "vue";

import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  APPEAL_STATUS_LABEL,
  POINTS_PROHIBITION_NOTICE,
  POINTS_USAGE_NOTICE,
  createPointsAppeal,
  fetchPointsAccount,
  fetchPointsAppeals,
  fetchPointsLedger,
  feedbackRewardRows,
  type PointsAccount,
  type PointsAppeal,
  type PointsLedgerEntry,
} from "@/modules/loyalty/api/loyalty";
import { useDocumentTitle } from "@/modules/commissions/api/commissions";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { PointsIcon, StarLoader } from "@/shared/ui/BrandMark";
import { formatDateTime, formatPoints, pointsWithUnit } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();
const setTitle = useDocumentTitle();

const account = ref<PointsAccount | null>(null);
const ledger = ref<PointsLedgerEntry[]>([]);
const ledgerTotal = ref(0);
const ledgerPage = ref(1);
const ledgerLimit = 20;
const appeals = ref<PointsAppeal[]>([]);

const loading = ref(true);
const ledgerLoading = ref(false);
const error = ref<string | null>(null);
const ledgerError = ref<string | null>(null);
const forbidden = ref(false);
const notFound = ref(false);

const appealOpen = ref(false);
const appealReason = ref("");
const appealEntryId = ref<string | null>(null);
const appealError = ref<string | null>(null);
const appealSaving = ref(false);

onMounted(async () => {
  setTitle("星绒币");
  void site.load();
  if (!auth.loaded) {
    await auth.loadMe().catch(() => null);
  }
  if (!auth.isAuthenticated) {
    forbidden.value = true;
    loading.value = false;
    return;
  }
  await Promise.all([loadAccount(), loadLedger(1), loadAppeals()]);
});

async function loadAccount(): Promise<void> {
  loading.value = true;
  error.value = null;
  notFound.value = false;
  try {
    account.value = await fetchPointsAccount();
  } catch (caught) {
    if (caught instanceof ApiError) {
      if (caught.needsLogin || caught.status === 403) forbidden.value = true;
      else if (caught.status === 404) notFound.value = true;
      else error.value = caught.message;
    } else {
      error.value = "星绒币账户加载失败。";
    }
  } finally {
    loading.value = false;
  }
}

async function loadLedger(page: number): Promise<void> {
  ledgerLoading.value = true;
  ledgerError.value = null;
  try {
    const payload = await fetchPointsLedger({ page, limit: ledgerLimit });
    ledger.value = page === 1 ? payload.items : [...ledger.value, ...payload.items];
    ledgerTotal.value = payload.total;
    ledgerPage.value = page;
  } catch (caught) {
    ledgerError.value =
      caught instanceof ApiError ? caught.message : "积分流水加载失败，可以重试。";
  } finally {
    ledgerLoading.value = false;
  }
}

async function loadAppeals(): Promise<void> {
  try {
    const payload = await fetchPointsAppeals();
    appeals.value = payload.items;
  } catch {
    // 申诉记录读取失败不阻塞主信息展示。
    appeals.value = [];
  }
}

const hasMoreLedger = computed(() => ledger.value.length < ledgerTotal.value && ledger.value.length >= ledgerLimit);

const rules = computed(() => site.loyaltyRules);

const rewardRows = computed(() => feedbackRewardRows(rules.value.feedback_rewards));

const negativeBalance = computed(() => (account.value?.posted_balance ?? 0) < 0);

function deltaTone(delta: number): "jade" | "wine" {
  return delta < 0 ? "wine" : "jade";
}

function deltaText(delta: number): string {
  return `${delta > 0 ? "+" : ""}${formatPoints(delta)}`;
}

function openAppeal(entry: PointsLedgerEntry | null): void {
  appealOpen.value = true;
  appealEntryId.value = entry?.id ?? null;
  appealReason.value = "";
  appealError.value = null;
}

async function submitAppeal(): Promise<void> {
  const reason = appealReason.value.trim();
  if (reason.length < 10) {
    appealError.value = "请写清你的疑问或依据，至少 10 个字。";
    return;
  }
  appealSaving.value = true;
  appealError.value = null;
  try {
    const appeal = await createPointsAppeal({
      ledger_entry_id: appealEntryId.value,
      reason,
    });
    appeals.value = [appeal, ...appeals.value];
    appealOpen.value = false;
    appealReason.value = "";
    toast.success("申诉已提交", "工作人员会核对账本与订单记录后回复。");
  } catch (caught) {
    appealError.value =
      caught instanceof ApiError
        ? caught.message
        : "申诉提交失败，请检查网络后重试。已填写的内容不会丢失。";
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
        { label: '星绒币' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">Starium points</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">星绒币</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        星绒币用于记录你在本站的消费与有效反馈。积分明细是权威记录，账户余额只是可以重建的缓存。
      </p>
    </header>

    <div v-if="loading" class="mt-8">
      <StarLoader message="正在读取星绒币账户" />
    </div>

    <EmptyState
      v-else-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="需要登录后查看"
      description="积分只对本人开放。登录后会回到这一页。"
    >
      <AppButton size="sm" to="/login">手机号登录</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="notFound"
      class="mt-8"
      tone="not-found"
      title="没有找到积分账户"
      description="账户可能在注销流程中。如有疑问请联系客服核对。"
    >
      <AppButton size="sm" to="/account">回到我的账户</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="error"
      class="mt-8"
      tone="error"
      title="星绒币账户加载失败"
      :description="`${error} 可以重试；积分记录不会因为打不开页面而丢失。`"
    >
      <AppButton size="sm" @click="loadAccount()">重新加载</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <template v-else-if="account">
      <!-- 1. 可用与待入账 -->
      <section class="mt-8 grid gap-6 lg:grid-cols-[1.2fr_0.8fr]">
        <SurfaceCard>
          <h2 class="font-display text-lg">可用与待入账</h2>
          <dl class="mt-5 grid gap-5 sm:grid-cols-2">
            <div>
              <dt class="text-xs text-ink-faint">可用星绒币</dt>
              <dd class="mt-2">
                <PointsIcon :size="24" :points="account.available_points" />
              </dd>
              <p class="mt-2 text-xs text-ink-faint">
                可用 = 账面余额 − 已冻结，最低为 0（冻结来自未完成订单的占用）。
              </p>
            </div>
            <div>
              <dt class="text-xs text-ink-faint">待入账</dt>
              <dd class="mt-2">
                <PointsIcon :size="24" :points="account.pending_points" />
              </dd>
              <p class="mt-2 text-xs text-ink-faint">
                待入账来自尚未生效的奖励，不能消费；订单完成交付后 {{ rules.release_delay_days }} 天且无未决售后才入账。
              </p>
            </div>
            <div>
              <dt class="text-xs text-ink-faint">账面余额</dt>
              <dd class="mt-2 flex flex-wrap items-baseline gap-2">
                <PointsIcon :size="20" :points="account.posted_balance" />
                <span v-if="negativeBalance" class="text-xs text-wine">账面为负</span>
              </dd>
            </div>
            <div>
              <dt class="text-xs text-ink-faint">已冻结</dt>
              <dd class="mt-2">
                <PointsIcon :size="20" :points="account.reserved_points" />
              </dd>
            </div>
          </dl>

          <p class="mt-5 text-xs text-ink-faint">规则版本：<span class="font-mono">{{ account.rules_version }}</span></p>

          <div v-if="negativeBalance" class="mt-5 rounded-lg border border-wine bg-wine-soft p-4 text-sm text-wine">
            <p class="font-medium">账面余额为负，原因是退款扣回</p>
            <p class="mt-2 text-xs leading-relaxed">
              订单发生退款时，已发放的奖励会按净额重新计算并扣回；如果奖励已经用过，账面就会变成负数。
              这不要求你支付现金，也不影响依法退款。后续获得奖励会先抵消欠扣。
            </p>
            <div class="mt-3">
              <AppButton size="sm" variant="secondary" @click="openAppeal(null)">对账目提出申诉</AppButton>
            </div>
          </div>
        </SurfaceCard>

        <SurfaceCard>
          <h2 class="font-display text-lg">使用范围</h2>
          <p class="mt-3 text-sm leading-relaxed text-ink-muted">{{ POINTS_USAGE_NOTICE }}</p>
          <p class="mt-3 rounded-sm bg-wine-soft px-4 py-3 text-xs leading-relaxed text-wine">
            {{ POINTS_PROHIBITION_NOTICE }}
          </p>
          <p v-if="!rules.redemption_enabled" class="mt-3 rounded-sm bg-canvas-sunk px-4 py-3 text-xs text-ink-muted">
            兑换与抵扣尚未开放，因此目前没有可兑换的权益或商品。开放条件与规则确认后会在这里说明。
          </p>
          <div class="mt-4">
            <AppButton size="sm" variant="secondary" @click="openAppeal(null)">提交积分申诉</AppButton>
          </div>
        </SurfaceCard>
      </section>

      <!-- 2. 获得方式 -->
      <section class="mt-12">
        <h2 class="font-display text-xl">获得方式</h2>
        <div class="mt-6 grid gap-6 lg:grid-cols-2">
          <SurfaceCard>
            <h3 class="font-display text-base">购买奖励</h3>
            <p class="mt-3 text-sm leading-relaxed text-ink-muted">
              每 {{ rules.denominator_yuan }} 元商品实付金额得 1 星绒币，向下取整。
              运费{{ rules.shipping_excluded ? "不计入" : "是否计入见规则" }}，
              退款、多付款、赠品与用券部分同样不计入。
            </p>
            <p class="mt-3 text-xs text-ink-faint">
              订单已付清、完成交付后 {{ rules.release_delay_days }} 天且没有未决售后，奖励才会入账；
              预付款、尾款与加项的支付不会分别触发多次完整奖励。
            </p>
            <p class="mt-3 text-xs text-ink-faint">
              发生退款时会按订单净额重算目标奖励：未入账的更新待入账，已入账的追加差额。
              取消与全额退款的目标为 0。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h3 class="font-display text-base">反馈奖励</h3>
            <ul class="mt-3 space-y-3 text-sm">
              <li v-for="row in rewardRows" :key="row.key" class="flex flex-wrap items-baseline justify-between gap-3">
                <span class="text-ink-muted">{{ row.label }}</span>
                <span class="shrink-0">
                  <PointsIcon :size="18" :points="row.points" />
                </span>
              </li>
            </ul>
            <p class="mt-4 text-xs text-ink-faint">
              奖励状态独立于反馈处理状态：有用反馈即使不能立刻修复也可以奖励。
              每天最多奖励 {{ rules.feedback_daily_reward_limit }} 条（预算草案），达到上限后仍可正常提交反馈，
              只是本次不再获奖。
            </p>
            <div class="mt-4">
              <AppButton size="sm" to="/feedback/new">提交反馈</AppButton>
            </div>
          </SurfaceCard>
        </div>
      </section>

      <!-- 3. 流水 -->
      <section class="mt-12">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <h2 class="font-display text-xl">积分流水</h2>
          <StatusBadge tone="neutral" :label="`共 ${ledgerTotal} 条`" />
        </div>
        <p class="mt-2 text-xs text-ink-faint">
          已发布的流水不可修改或删除；纠错通过追加调整或冲正完成，并关联原流水、理由与操作者。
        </p>

        <div v-if="ledgerLoading && ledger.length === 0" class="mt-6">
          <SurfaceCard>
            <SkeletonBlock :lines="4" aria-label="正在加载积分流水" />
          </SurfaceCard>
        </div>

        <EmptyState
          v-else-if="ledgerError && ledger.length === 0"
          class="mt-6"
          tone="error"
          title="积分流水加载失败"
          :description="`${ledgerError} 可以重试。`"
        >
          <AppButton size="sm" @click="loadLedger(1)">重新加载</AppButton>
        </EmptyState>

        <EmptyState
          v-else-if="ledger.length === 0"
          class="mt-6"
          title="还没有积分流水"
          description="完成订单交付或有效反馈入账后，明细会出现在这里；我们不会用预估数值填充流水。"
        >
          <AppButton size="sm" variant="secondary" to="/shop">去商城看看</AppButton>
        </EmptyState>

        <div v-else class="mt-6 overflow-x-auto">
          <table class="w-full min-w-[640px] border-collapse text-sm">
            <caption class="sr-only">积分流水明细</caption>
            <thead>
              <tr class="border-b border-line text-left text-xs text-ink-faint">
                <th scope="col" class="py-2 pr-4 font-medium">发生时间</th>
                <th scope="col" class="py-2 pr-4 font-medium">业务说明</th>
                <th scope="col" class="py-2 pr-4 font-medium">来源</th>
                <th scope="col" class="py-2 pr-4 text-right font-medium">变动</th>
                <th scope="col" class="py-2 text-right font-medium">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr v-for="entry in ledger" :key="entry.id" class="border-b border-line align-top">
                <td class="py-3 pr-4 tabular-nums text-ink-muted">{{ formatDateTime(entry.occurred_at) }}</td>
                <td class="py-3 pr-4">
                  <span class="text-ink-soft">{{ entry.reason_label }}</span>
                  <span v-if="entry.posted_at === null" class="mt-1 block text-xs text-ink-faint">
                    尚未入账
                  </span>
                  <span v-if="entry.related_entry_id" class="mt-1 block text-xs text-ink-faint">
                    关联冲正记录 {{ entry.related_entry_id }}
                  </span>
                </td>
                <td class="py-3 pr-4 text-ink-muted">{{ entry.source_summary || "—" }}</td>
                <td class="py-3 pr-4 text-right">
                  <StatusBadge :tone="deltaTone(entry.delta)" :label="deltaText(entry.delta)" />
                  <span class="sr-only">{{ pointsWithUnit(entry.delta) }}</span>
                </td>
                <td class="py-3 text-right">
                  <AppButton size="sm" variant="quiet" @click="openAppeal(entry)">申诉</AppButton>
                </td>
              </tr>
            </tbody>
          </table>
        </div>

        <div v-if="hasMoreLedger" class="mt-5">
          <AppButton variant="secondary" :loading="ledgerLoading" @click="loadLedger(ledgerPage + 1)">
            加载更多流水
          </AppButton>
        </div>
      </section>

      <!-- 4. 规则与申诉 -->
      <section class="mt-12 grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <SurfaceCard>
          <h2 class="font-display text-xl">规则</h2>
          <ul class="mt-4 space-y-3 text-sm text-ink-muted">
            <li>换算：每 {{ rules.denominator_yuan }} 元商品实付金额 = 1 星绒币，向下取整。</li>
            <li>运费{{ rules.shipping_excluded ? "不计入" : "是否计入见规则说明" }}奖励计算。</li>
            <li>订单完成交付后 {{ rules.release_delay_days }} 天且没有未决售后，奖励入账。</li>
            <li>
              反馈奖励：
              {{ rules.feedback_rewards.minor }} / {{ rules.feedback_rewards.normal }} /
              {{ rules.feedback_rewards.major }} 星绒币，按有效性与证据强度审核。
            </li>
            <li>每天最多奖励 {{ rules.feedback_daily_reward_limit }} 条；以 Asia/Shanghai 计算自然日边界，入账时间存 UTC。</li>
            <li>规则调整不回溯重算老订单；订单在下单时保存当时的规则版本。</li>
            <li v-if="!rules.redemption_enabled">兑换与抵扣尚未开放；开放前不产生可兑换权益。</li>
          </ul>
          <p class="mt-5 rounded-sm bg-canvas-sunk px-4 py-3 text-xs leading-relaxed text-ink-muted">
            同一实际问题原则上奖励首个有效报告；相似性只辅助审核，不自动判定抄袭。
            撤回反馈不会自动删除已发积分，确认作弊才会通过复核追加冲正。
          </p>
        </SurfaceCard>

        <div class="space-y-6">
          <SurfaceCard>
            <h2 class="font-display text-xl">申诉</h2>
            <p class="mt-3 text-sm text-ink-muted">
              对某笔流水、扣回金额或奖励判定有疑问时，可以提交申诉。申诉会关联到具体流水，并可以查看处理结果。
            </p>
            <div class="mt-4">
              <AppButton @click="openAppeal(null)">提交申诉</AppButton>
            </div>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">我的申诉记录</h2>
            <EmptyState
              v-if="appeals.length === 0"
              class="mt-3"
              title="还没有申诉记录"
              description="提交后会在这里看到处理状态与结论。"
            />
            <ul v-else class="mt-3 divide-y divide-line">
              <li v-for="appeal in appeals" :key="appeal.id" class="py-3">
                <div class="flex flex-wrap items-center justify-between gap-2">
                  <StatusBadge tone="neutral" :label="APPEAL_STATUS_LABEL[appeal.status] ?? appeal.status" />
                  <span class="text-xs tabular-nums text-ink-faint">{{ formatDateTime(appeal.created_at) }}</span>
                </div>
                <p class="mt-2 text-sm text-ink-muted">{{ appeal.reason }}</p>
                <p v-if="appeal.resolution_note" class="mt-1 text-xs text-ink-faint">结论：{{ appeal.resolution_note }}</p>
              </li>
            </ul>
          </SurfaceCard>
        </div>
      </section>

      <!-- 申诉表单 -->
      <SurfaceCard v-if="appealOpen" class="mt-8">
        <h2 class="font-display text-lg">提交积分申诉</h2>
        <p class="mt-2 text-xs text-ink-faint">
          {{ appealEntryId ? `关联流水 ${appealEntryId}` : "不关联具体流水（对规则或整体账目有疑问）" }}
        </p>
        <div class="mt-4">
          <FieldRow
            label="申诉说明"
            for="appeal-reason"
            required
            :error="appealError ?? ''"
            hint="写清你看到的数值、你认为应有的结果与依据，至少 10 个字。"
          >
            <textarea id="appeal-reason" v-model="appealReason" class="field-base min-h-[120px]" maxlength="2000" />
          </FieldRow>
        </div>
        <div class="mt-4 flex flex-wrap gap-3">
          <AppButton :loading="appealSaving" @click="submitAppeal">提交申诉</AppButton>
          <AppButton variant="secondary" @click="appealOpen = false">取消</AppButton>
        </div>
      </SurfaceCard>
    </template>
  </div>
</template>
