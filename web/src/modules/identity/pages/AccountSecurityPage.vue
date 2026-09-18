<script setup lang="ts">
/**
 * 账号与安全（`/account/security`）。
 *
 * 依据 03/10 文档：
 * - 换绑手机号：旧号 + 新号双向验证；旧号丢失走人工申诉，必须提交独立身份/交易证明，
 *   不能只凭知道订单号换绑；
 * - 会话列表：可撤销单个会话、全端退出（撤销除当前之外的全部会话）；
 * - 注销申请：展示服务端返回的 `blockers` 与处理路径，说明账号立即停止服务、
 *   可删除资料进入清理、交易与合同按法定义务隔离保留；
 * - 敏感动作前用短信验证码重新验证（`/me/sensitive-verify`，窗口 10 分钟）。
 */
import { computed, onMounted, ref } from "vue";

import CountdownButton from "@/modules/identity/components/CountdownButton.vue";
import OtpInput from "@/modules/identity/components/OtpInput.vue";
import {
  confirmPhoneChange,
  describeIdentityError,
  errorCodeOf,
  fetchMySessions,
  isSessionExpired,
  isSixDigitCode,
  normalizePhoneForSubmit,
  phoneFormatError,
  requestAccountClosure,
  requestPhoneChangeChallenge,
  requiresSensitiveReauth,
  revokeSession,
  verifySensitiveChallenge,
  type ClosureResult,
  type PhoneChangeChallenge,
  type UserSession,
} from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime, formatRelative, truncate } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAuthStore();
const site = useSiteStore();
const toast = useToast();

const crumbs = [
  { label: "首页", to: "/" },
  { label: "我的账户", to: "/account" },
  { label: "账号与安全" },
];

// ---------------------------------------------------------------- 会话
const sessions = ref<UserSession[]>([]);
const sessionsLoading = ref(true);
const sessionsError = ref("");
const sessionsForbidden = ref(false);
const sessionBusy = ref("");
const revokeOthersBusy = ref(false);

// ---------------------------------------------------------------- 敏感重新验证
const sensitiveOpen = ref(false);
const sensitiveChallenge = ref("");
const sensitiveCode = ref("");
const sensitiveError = ref("");
const sensitiveNotice = ref("");
const sensitiveVerifying = ref(false);
const sensitiveCountdown = ref<{ start: (seconds: number) => void } | null>(null);
const sensitiveOtp = ref<{ focus: () => void; reset: () => void } | null>(null);
/** 验证成功后要自动继续的敏感动作。 */
let pendingAction: (() => Promise<void>) | null = null;

// ---------------------------------------------------------------- 换绑手机号
const oldChallenge = ref<PhoneChangeChallenge | null>(null);
const newChallenge = ref<PhoneChangeChallenge | null>(null);
const oldCode = ref("");
const newCode = ref("");
const newPhone = ref("");
const newDialCode = ref("+86");
const phoneBusy = ref(false);
const phoneError = ref("");
const phoneNotice = ref("");
const oldSent = ref(false);
const newSent = ref(false);
const oldCountdown = ref<{ start: (seconds: number) => void } | null>(null);
const newCountdown = ref<{ start: (seconds: number) => void } | null>(null);

// ---------------------------------------------------------------- 人工申诉
const appealOpen = ref(false);
const appealNote = ref("");
const appealCode = ref("");
const appealChallenge = ref<PhoneChangeChallenge | null>(null);
const appealBusy = ref(false);
const appealError = ref("");

// ---------------------------------------------------------------- 注销
const closureOpen = ref(false);
const closureReason = ref("");
const closureCode = ref("");
const closureChallenge = ref<PhoneChangeChallenge | null>(null);
const closureChallengeSent = ref(false);
const closureAcknowledged = ref(false);
const closureBusy = ref(false);
const closureError = ref("");
const closureResult = ref<ClosureResult | null>(null);
const closureCountdown = ref<{ start: (seconds: number) => void } | null>(null);

const otherSessions = computed(() => sessions.value.filter((item) => !item.current));

function deviceLabel(userAgent: string | null): string {
  if (!userAgent) return "未知设备";
  const browser = /Edg\//.test(userAgent)
    ? "Edge"
    : /Chrome\//.test(userAgent)
      ? "Chrome"
      : /Safari\//.test(userAgent)
        ? "Safari"
        : /Firefox\//.test(userAgent)
          ? "Firefox"
          : "浏览器";
  const system = /Windows/.test(userAgent)
    ? "Windows"
    : /Mac OS X|Macintosh/.test(userAgent)
      ? "macOS"
      : /Android/.test(userAgent)
        ? "Android"
        : /iPhone|iPad|iOS/.test(userAgent)
          ? "iOS"
          : /Linux/.test(userAgent)
            ? "Linux"
            : "未知系统";
  return `${system} · ${browser}`;
}

async function loadSessions(): Promise<void> {
  sessionsLoading.value = true;
  sessionsError.value = "";
  sessionsForbidden.value = false;
  try {
    sessions.value = await fetchMySessions();
  } catch (caught) {
    sessions.value = [];
    sessionsError.value = describeIdentityError(caught);
    sessionsForbidden.value = errorCodeOf(caught) === "FORBIDDEN";
  } finally {
    sessionsLoading.value = false;
  }
}

async function handleSessionFailure(caught: unknown, retry: () => Promise<void>): Promise<void> {
  if (requiresSensitiveReauth(caught)) {
    openSensitive("该操作需要重新验证手机号。", retry);
    return;
  }
  if (isSessionExpired(caught)) {
    sessionsError.value = "登录状态已过期，请重新登录后继续。";
    return;
  }
  const message = describeIdentityError(caught);
  toast.error("操作未完成", message);
}

async function revokeOne(session: UserSession): Promise<void> {
  sessionBusy.value = session.id;
  try {
    await revokeSession(session.id);
    toast.success("已撤销该会话", "该设备需要重新登录。");
    await loadSessions();
  } catch (caught) {
    await handleSessionFailure(caught, () => revokeOne(session));
  } finally {
    sessionBusy.value = "";
  }
}

async function revokeOthers(): Promise<void> {
  if (!otherSessions.value.length) return;
  revokeOthersBusy.value = true;
  try {
    const revoked = await auth.revokeOtherSessions();
    toast.success("已全端退出", `撤销了 ${revoked} 个其他会话，当前会话保留。`);
    await loadSessions();
  } catch (caught) {
    await handleSessionFailure(caught, revokeOthers);
  } finally {
    revokeOthersBusy.value = false;
  }
}

// ---------------------------------------------------------------- 敏感重新验证
function openSensitive(notice: string, next: (() => Promise<void>) | null): void {
  pendingAction = next;
  sensitiveNotice.value = notice;
  sensitiveOpen.value = true;
  sensitiveError.value = "";
}

async function sendSensitiveCode(): Promise<void> {
  // 敏感动作的验证码由服务端按**会话绑定的真实号码**下发（`role="sensitive"`），
  // 前端不提交手机号：页面上只有脱敏号（`+861380***0000`），既不能做格式校验，
  // 也不能当作真实号码提交（否则服务端会按非法号码拒绝）。
  if (!auth.user?.phone_masked) {
    sensitiveError.value = "无法读取当前手机号，请刷新页面后重试。";
    return;
  }
  sensitiveError.value = "";
  sensitiveVerifying.value = true;
  try {
    const challenge = await requestPhoneChangeChallenge("sensitive");
    sensitiveChallenge.value = challenge.challenge_id;
    sensitiveOtp.value?.focus();
    sensitiveCountdown.value?.start(challenge.retry_after);
  } catch (caught) {
    sensitiveError.value = describeIdentityError(caught);
    if (errorCodeOf(caught) === "SMS_RATE_LIMITED") sensitiveCountdown.value?.start(60);
  } finally {
    sensitiveVerifying.value = false;
  }
}

async function verifySensitive(): Promise<void> {
  sensitiveError.value = "";
  if (!sensitiveChallenge.value) {
    sensitiveError.value = "请先获取短信验证码。";
    return;
  }
  if (!isSixDigitCode(sensitiveCode.value)) {
    sensitiveError.value = "请输入 6 位数字验证码。";
    return;
  }
  sensitiveVerifying.value = true;
  try {
    // 验证码由服务端核验；只有核验通过才允许执行敏感动作（不做前端「假通过」）。
    await verifySensitiveChallenge(sensitiveChallenge.value, sensitiveCode.value);
    sensitiveChallenge.value = "";
    sensitiveOpen.value = false;
    sensitiveCode.value = "";
    toast.success("验证通过", "10 分钟内可以执行换绑与注销等敏感操作。");
    const next = pendingAction;
    pendingAction = null;
    if (next) await next();
  } catch (caught) {
    sensitiveCode.value = "";
    sensitiveOtp.value?.reset();
    sensitiveError.value = describeIdentityError(caught);
  } finally {
    sensitiveVerifying.value = false;
  }
}

// ---------------------------------------------------------------- 换绑手机号
async function sendOldCode(): Promise<void> {
  phoneError.value = "";
  oldSent.value = true;
  try {
    const challenge = await requestPhoneChangeChallenge("old");
    oldChallenge.value = challenge;
    oldCountdown.value?.start(challenge.retry_after);
  } catch (caught) {
    if (requiresSensitiveReauth(caught)) {
      openSensitive("换绑手机号前需要重新验证当前手机号。", sendOldCode);
      oldSent.value = false;
      return;
    }
    phoneError.value = describeIdentityError(caught);
    if (errorCodeOf(caught) === "SMS_RATE_LIMITED") oldCountdown.value?.start(60);
  }
}

async function sendNewCode(): Promise<void> {
  phoneError.value = "";
  const formatError = phoneFormatError(newPhone.value);
  if (formatError) {
    phoneError.value = `新手机号格式不正确：${formatError.replace(/^手机号格式不正确：/, "")}`;
    return;
  }
  newSent.value = true;
  try {
    const challenge = await requestPhoneChangeChallenge(
      "new",
      normalizePhoneForSubmit(newDialCode.value, newPhone.value),
    );
    newChallenge.value = challenge;
    newCountdown.value?.start(challenge.retry_after);
  } catch (caught) {
    newSent.value = false;
    phoneError.value = describeIdentityError(caught);
    if (errorCodeOf(caught) === "SMS_RATE_LIMITED") newCountdown.value?.start(60);
  }
}

async function submitPhoneChange(manualAppeal = false): Promise<void> {
  phoneError.value = "";
  phoneNotice.value = "";
  if (!newChallenge.value) {
    phoneError.value = "请先获取新手机号的验证码。";
    return;
  }
  if (!isSixDigitCode(newCode.value)) {
    phoneError.value = "请输入新手机号收到的 6 位验证码。";
    return;
  }
  if (!manualAppeal) {
    if (!oldChallenge.value) {
      phoneError.value = "请先获取当前手机号的验证码。";
      return;
    }
    if (!isSixDigitCode(oldCode.value)) {
      phoneError.value = "请输入当前手机号收到的 6 位验证码。";
      return;
    }
  }
  phoneBusy.value = true;
  try {
    await confirmPhoneChange({
      ...(manualAppeal
        ? { manual_appeal: true, evidence_note: appealNote.value.trim() }
        : { old_challenge_id: oldChallenge.value?.challenge_id, old_code: oldCode.value }),
      new_challenge_id: newChallenge.value.challenge_id,
      new_code: newCode.value,
    });
    if (manualAppeal) {
      phoneNotice.value =
        "人工申诉已提交。工作人员会核验你的独立身份证明与交易证明后联系你，仅凭订单号不能完成换绑。";
    } else {
      phoneNotice.value = "换绑成功：其他设备的会话已按策略撤销，请用新手机号重新登录。";
      toast.success("手机号已换绑", "请使用新手机号重新登录。");
      await loadSessions();
    }
    oldChallenge.value = null;
    newChallenge.value = null;
    appealChallenge.value = null;
    oldCode.value = "";
    newCode.value = "";
    appealCode.value = "";
  } catch (caught) {
    phoneError.value = describeIdentityError(caught);
  } finally {
    phoneBusy.value = false;
  }
}

async function sendAppealCode(): Promise<void> {
  appealError.value = "";
  const formatError = phoneFormatError(newPhone.value);
  if (formatError) {
    appealError.value = `请先填写可接收短信的新手机号。${formatError}`;
    return;
  }
  try {
    const challenge = await requestPhoneChangeChallenge(
      "new",
      normalizePhoneForSubmit(newDialCode.value, newPhone.value),
    );
    appealChallenge.value = challenge;
    appealCode.value = "";
    newChallenge.value = challenge;
  } catch (caught) {
    appealError.value = describeIdentityError(caught);
  }
}

async function submitAppeal(): Promise<void> {
  appealError.value = "";
  if (appealNote.value.trim().length < 10) {
    appealError.value = "请说明可核验的独立证明（例如合同编号、付款账户、原始订单与联系记录），至少 10 个字。";
    return;
  }
  appealBusy.value = true;
  try {
    await submitPhoneChange(true);
    if (phoneError.value) appealError.value = phoneError.value;
  } finally {
    appealBusy.value = false;
  }
}

// ---------------------------------------------------------------- 注销
async function sendClosureCode(): Promise<void> {
  closureError.value = "";
  // 同 sendSensitiveCode：注销验证码也走「服务端按会话号码下发」，不提交脱敏号。
  if (!auth.user?.phone_masked) {
    closureError.value = "无法读取当前手机号，请刷新页面后重试。";
    return;
  }
  try {
    const challenge = await requestPhoneChangeChallenge("sensitive");
    closureChallenge.value = challenge;
    closureChallengeSent.value = true;
    closureCountdown.value?.start(challenge.retry_after);
  } catch (caught) {
    if (requiresSensitiveReauth(caught)) {
      openSensitive("注销申请前需要重新验证当前手机号。", sendClosureCode);
      return;
    }
    closureError.value = describeIdentityError(caught);
  }
}

async function submitClosure(): Promise<void> {
  closureError.value = "";
  if (!closureChallenge.value) {
    closureError.value = "请先获取验证码。";
    return;
  }
  if (!isSixDigitCode(closureCode.value)) {
    closureError.value = "请输入 6 位数字验证码。";
    return;
  }
  closureBusy.value = true;
  try {
    const result = await requestAccountClosure({
      reason: closureReason.value.trim() || undefined,
      challenge_id: closureChallenge.value.challenge_id,
      code: closureCode.value,
      acknowledged_blockers: closureAcknowledged.value,
    });
    closureResult.value = result;
    closureCode.value = "";
    if (result.status === "BLOCKED") {
      closureAcknowledged.value = false;
    } else {
      toast.info("注销申请已受理", result.message);
      await auth.logout().catch(() => undefined);
    }
  } catch (caught) {
    if (requiresSensitiveReauth(caught)) {
      openSensitive("注销申请前需要重新验证当前手机号。", submitClosure);
      return;
    }
    closureError.value = describeIdentityError(caught);
  } finally {
    closureBusy.value = false;
  }
}

onMounted(() => {
  void site.load();
  void loadSessions();
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="crumbs" />

    <SectionHeading
      class="mt-4"
      :level="1"
      eyebrow="Security"
      title="账号与安全"
      description="换绑、注销与会话撤销属于敏感操作：需要先用短信验证码重新验证身份，验证窗口 10 分钟。"
    />

    <div class="mt-6 flex flex-wrap items-center gap-3">
      <StatusBadge tone="neutral" :label="`当前手机号 ${auth.user?.phone_masked ?? '—'}`" />
      <StatusBadge
        :tone="auth.sensitiveVerified ? 'jade' : 'amber'"
        :label="auth.sensitiveVerified ? '敏感操作已重新验证' : '敏感操作需要重新验证'"
      />
    </div>

    <!-- 敏感重新验证面板 -->
    <SurfaceCard v-if="sensitiveOpen" as="section" class="mt-6" aria-labelledby="sensitive-heading">
      <h2 id="sensitive-heading" class="font-display text-xl">重新验证身份</h2>
      <p class="mt-2 text-sm text-ink-muted">{{ sensitiveNotice }}</p>
      <p class="mt-1 text-xs text-ink-faint">
        验证码发送到 {{ auth.user?.phone_masked ?? "当前手机号" }}；我们不会向任何索要验证码的人提供你的资料。
      </p>

      <div class="mt-4 flex flex-wrap items-end gap-4">
        <CountdownButton
          ref="sensitiveCountdown"
          :loading="sensitiveVerifying"
          label="发送验证码"
          @send="sendSensitiveCode"
        />
        <div class="min-w-[16rem] flex-1">
          <OtpInput
            ref="sensitiveOtp"
            v-model="sensitiveCode"
            label="短信验证码"
            :disabled="!sensitiveChallenge"
            :error="sensitiveError"
            :hint="sensitiveChallenge ? '验证码 5 分钟内有效，只能使用一次。' : '请先点击「发送验证码」。'"
          />
        </div>
      </div>

      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton :loading="sensitiveVerifying" :disabled="!sensitiveChallenge" :disabled-reason="sensitiveChallenge ? '' : '请先发送验证码。'" @click="verifySensitive()">
          完成验证
        </AppButton>
        <AppButton variant="secondary" @click="sensitiveOpen = false; pendingAction = null">取消</AppButton>
      </div>
    </SurfaceCard>

    <div class="mt-8 grid gap-6 lg:grid-cols-2">
      <!-- 换绑手机号 -->
      <SurfaceCard as="section" aria-labelledby="phone-heading">
        <h2 id="phone-heading" class="font-display text-xl">换绑手机号</h2>
        <p class="mt-2 text-xs text-ink-faint">
          默认需要同时验证当前手机号与新手机号。换绑后其他设备的会话会被撤销，并通知原号码。
        </p>

        <div class="mt-5 space-y-5">
          <div class="rounded-sm border border-line bg-surface-muted p-4">
            <p class="text-sm font-medium">第一步：验证当前手机号</p>
            <p class="mt-1 text-xs text-ink-faint">{{ auth.user?.phone_masked ?? "—" }}</p>
            <div class="mt-3 flex flex-wrap items-end gap-3">
              <CountdownButton ref="oldCountdown" label="发送验证码" size="sm" @send="sendOldCode" />
              <div class="min-w-[12rem] flex-1">
                <OtpInput v-model="oldCode" label="当前手机号验证码" :disabled="!oldChallenge" />
              </div>
            </div>
            <p v-if="!oldChallenge && oldSent" class="mt-2 text-xs text-ink-faint">
              正在等待发送结果；失败时会在下方给出原因。
            </p>
          </div>

          <div class="rounded-sm border border-line bg-surface-muted p-4">
            <p class="text-sm font-medium">第二步：验证新手机号</p>
            <div class="mt-3 grid gap-3 sm:grid-cols-[9rem_1fr]">
              <label class="space-y-1.5 text-sm">
                <span class="block font-medium text-ink-soft">区号</span>
                <select v-model="newDialCode" class="field-base" name="new-dial">
                  <!-- 与服务端白名单（SMS_REGIONS，默认 CN,US）一致：C02/C24 首发中国大陆 + 美国 -->
                  <option value="+86">中国大陆 +86</option>
                  <option value="+1">美国 / 加拿大 +1</option>
                </select>
              </label>
              <FieldRow label="新手机号" for="new-phone" required>
                <input
                  id="new-phone"
                  v-model.trim="newPhone"
                  class="field-base tabular-nums"
                  type="tel"
                  inputmode="numeric"
                  maxlength="11"
                  autocomplete="tel-national"
                  placeholder="11 位手机号"
                />
              </FieldRow>
            </div>
            <div class="mt-3 flex flex-wrap items-end gap-3">
              <CountdownButton ref="newCountdown" label="发送验证码" size="sm" @send="sendNewCode" />
              <div class="min-w-[12rem] flex-1">
                <OtpInput v-model="newCode" label="新手机号验证码" :disabled="!newChallenge" />
              </div>
            </div>
            <p v-if="!newChallenge && newSent" class="mt-2 text-xs text-ink-faint">
              正在等待发送结果；失败时会在下方给出原因。
            </p>
          </div>

          <p v-if="phoneError" class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
            {{ phoneError }}
          </p>
          <p v-if="phoneNotice" class="rounded-sm border border-jade bg-jade-soft px-3 py-2 text-sm text-jade" role="status">
            {{ phoneNotice }}
          </p>

          <div class="flex flex-wrap gap-3">
            <AppButton
              :loading="phoneBusy"
              :disabled="!oldChallenge || !newChallenge"
              :disabled-reason="
                !oldChallenge && !newChallenge
                  ? '请先分别验证当前手机号与新手机号。'
                  : !oldChallenge
                    ? '请先验证当前手机号。'
                    : !newChallenge
                      ? '请先验证新手机号。'
                      : ''
              "
              @click="submitPhoneChange(false)"
            >
              确认换绑
            </AppButton>
            <AppButton variant="secondary" @click="appealOpen = !appealOpen">
              {{ appealOpen ? "收起人工申诉" : "旧号已丢失？走人工申诉" }}
            </AppButton>
          </div>

          <!-- 人工申诉：不能只凭知道订单号换绑 -->
          <div v-if="appealOpen" class="rounded-sm border border-amber bg-amber-soft p-4 text-sm text-amber-strong">
            <p class="font-medium">人工申诉需要独立身份或交易证明</p>
            <ul class="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed">
              <li>知道订单号、收件地址或聊天记录截图，都不足以单独证明账号归属。</li>
              <li>受理后会核对：付款账户实名信息、合同/报价版本与签署记录、原始联系方式的可达性。</li>
              <li>核验期间原账号保持可用但冻结高风险变更；核验不通过会说明理由与复议路径。</li>
            </ul>
            <div class="mt-3">
              <FieldRow
                label="可核验的证明材料说明"
                for="appeal-note"
                required
                hint="请写明合同编号、付款账户后四位、原始下单时间等可交叉核对的信息（10–500 字）。"
              >
                <textarea
                  id="appeal-note"
                  v-model="appealNote"
                  class="field-base min-h-[6rem]"
                  maxlength="500"
                  placeholder="例：合同编号 CON-2026-xxxx，付款账户尾号 1234，2026-03-12 通过微信支付预付款。"
                />
              </FieldRow>
            </div>
            <div class="mt-3 flex flex-wrap items-end gap-3">
              <AppButton size="sm" variant="secondary" @click="sendAppealCode()">发送新号验证码</AppButton>
              <div class="min-w-[12rem] flex-1">
                <OtpInput v-model="newCode" label="新手机号验证码" :disabled="!newChallenge" :error="appealError" />
              </div>
              <AppButton size="sm" :loading="appealBusy || phoneBusy" :disabled="!newChallenge" :disabled-reason="newChallenge ? '' : '请先获取新号验证码。'" @click="submitAppeal()">
                提交申诉
              </AppButton>
            </div>
          </div>
        </div>
      </SurfaceCard>

      <!-- 会话管理 -->
      <SurfaceCard as="section" aria-labelledby="sessions-heading">
        <div class="flex flex-wrap items-start justify-between gap-3">
          <h2 id="sessions-heading" class="font-display text-xl">登录设备与会话</h2>
          <AppButton
            size="sm"
            variant="secondary"
            :loading="revokeOthersBusy"
            :disabled="otherSessions.length === 0"
            :disabled-reason="otherSessions.length === 0 ? '当前只有本设备登录。' : ''"
            @click="revokeOthers()"
          >
            全端退出（保留本机）
          </AppButton>
        </div>

        <div class="mt-4">
          <SkeletonBlock v-if="sessionsLoading" :lines="3" aria-label="正在读取登录会话" />
          <EmptyState
            v-else-if="sessionsForbidden"
            tone="forbidden"
            title="当前账号无权查看会话列表"
            description="请确认登录的账号；如需协助，请通过公开客服邮箱联系。"
          />
          <p v-else-if="sessionsError" class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
            {{ sessionsError }}
            <AppButton class="ml-3" size="sm" variant="quiet" @click="loadSessions()">重试</AppButton>
          </p>
          <EmptyState
            v-else-if="sessions.length === 0"
            title="没有可显示的会话"
            description="会话记录只保留仍在有效期内的登录；已退出与已撤销的记录会按安全保留期清理。"
          />
          <ul v-else class="space-y-3">
            <li
              v-for="item in sessions"
              :key="item.id"
              class="rounded-sm border border-line bg-surface-muted p-4"
            >
              <div class="flex flex-wrap items-center gap-2">
                <StatusBadge :tone="item.current ? 'jade' : 'neutral'" :label="item.current ? '当前设备' : '其他设备'" />
                <StatusBadge tone="neutral" :label="item.scope === 'admin' ? '员工会话' : '客户会话'" />
              </div>
              <p class="mt-2 text-sm font-medium">{{ deviceLabel(item.user_agent) }}</p>
              <p class="mt-1 text-xs text-ink-faint" :title="item.user_agent ?? ''">
                {{ truncate(item.user_agent ?? "未记录设备信息", 90) }}
              </p>
              <dl class="mt-2 grid gap-1 text-xs text-ink-muted sm:grid-cols-2">
                <div>最近活跃：{{ formatRelative(item.last_seen_at) }}</div>
                <div>登录时间：{{ formatDateTime(item.created_at) }}</div>
                <div>到期时间：{{ formatDateTime(item.expires_at) }}</div>
              </dl>
              <div class="mt-3">
                <AppButton
                  size="sm"
                  variant="danger"
                  :loading="sessionBusy === item.id"
                  :disabled="item.current"
                  :disabled-reason="item.current ? '撤销当前会话会立即退出登录，请改用页面右上角的退出。' : ''"
                  @click="revokeOne(item)"
                >
                  {{ item.current ? "当前会话" : "撤销该会话" }}
                </AppButton>
              </div>
            </li>
          </ul>
        </div>

        <p class="mt-4 text-xs text-ink-faint">
          会话使用 HttpOnly Cookie，令牌不写入前端存储；登录、换绑与注销后按策略轮换或撤销。
          发现异常登录时，先撤销其他会话，再换绑手机号。
        </p>
      </SurfaceCard>
    </div>

    <!-- 注销申请 -->
    <SurfaceCard as="section" class="mt-6" aria-labelledby="closure-heading">
      <h2 id="closure-heading" class="font-display text-xl">注销账号</h2>
      <p class="mt-2 text-sm leading-relaxed text-ink-muted">
        注销后：账号立即停止服务并撤销全部会话；非必要资料进入可删除的清理流程；
        订单、付款、退款、合同与发票按适用法定义务隔离保留，仅为法定义务访问。
        未完结订单与在办售后需要先处理或确认知悉，我们不会以任意理由永久拒绝注销。
      </p>

      <div class="mt-4">
        <AppButton variant="danger" @click="closureOpen = !closureOpen">
          {{ closureOpen ? "收起注销申请" : "申请注销账号" }}
        </AppButton>
      </div>

      <div v-if="closureOpen" class="mt-5 space-y-4">
        <div v-if="closureResult?.blockers?.length" class="rounded-sm border border-amber bg-amber-soft p-4 text-sm text-amber-strong">
          <p class="font-medium">注销前需要先处理以下事项</p>
          <ul class="mt-2 list-disc space-y-1 pl-5 text-xs leading-relaxed">
            <li v-for="(blocker, index) in closureResult.blockers" :key="index">{{ blocker }}</li>
          </ul>
          <p class="mt-2 text-xs leading-relaxed">
            处理路径：在
            <RouterLink class="link-underline" to="/account/orders">我的订单</RouterLink>、
            <RouterLink class="link-underline" to="/account/quotes">询价与报价</RouterLink>、
            <RouterLink class="link-underline" to="/account/after-sales">售后</RouterLink>
            中完成或取消相应事项后重新提交；若你确认知悉并坚持注销，可勾选下方确认后再次提交，
            交易与合同记录仍会按法定义务隔离保留。
          </p>
        </div>

        <p v-if="closureResult && closureResult.status !== 'BLOCKED'" class="rounded-sm border border-jade bg-jade-soft px-3 py-2 text-sm text-jade" role="status">
          {{ closureResult.message }}
          <span v-if="closureResult.scheduled_for" class="ml-1">
            资料清理计划于 {{ formatDateTime(closureResult.scheduled_for) }} 前完成。
          </span>
        </p>

        <FieldRow label="注销原因（可选）" for="closure-reason" hint="用于改进服务；不填写不影响受理。">
          <textarea id="closure-reason" v-model="closureReason" class="field-base min-h-[5rem]" maxlength="300" />
        </FieldRow>

        <label class="flex items-start gap-3 text-sm text-ink-soft">
          <input v-model="closureAcknowledged" type="checkbox" class="mt-1 size-4 shrink-0 accent-amber" />
          <span>
            我已知悉注销的后果：账号立即停止服务、会话全部撤销、非必要资料进入清理；
            交易与合同记录按法定义务隔离保留，且注销不影响已生效的售后与退款权利。
          </span>
        </label>

        <div class="flex flex-wrap items-end gap-4">
          <CountdownButton
            ref="closureCountdown"
            label="发送验证码"
            :disabled-reason="closureAcknowledged ? '' : '请先勾选上方确认。'"
            :disabled="!closureAcknowledged"
            @send="sendClosureCode"
          />
          <div class="min-w-[16rem] flex-1">
            <OtpInput v-model="closureCode" label="短信验证码" :disabled="!closureChallengeSent" :error="closureError" />
          </div>
        </div>

        <div class="flex flex-wrap gap-3">
          <AppButton
            variant="danger"
            :loading="closureBusy"
            :disabled="!closureChallengeSent || !closureAcknowledged"
            :disabled-reason="
              !closureAcknowledged
                ? '请先勾选确认知悉注销后果。'
                : !closureChallengeSent
                  ? '请先获取验证码。'
                  : ''
            "
            @click="submitClosure()"
          >
            提交注销申请
          </AppButton>
          <AppButton variant="secondary" to="/contact">先咨询客服</AppButton>
        </div>

        <p class="text-xs text-ink-faint">
          注销或数据权利请求也可以不登录提出：发送邮件到
          <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>，
          我们会登记办理状态与理由。
        </p>
      </div>
    </SurfaceCard>
  </div>
</template>
