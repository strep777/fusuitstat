<script setup lang="ts">
/**
 * 手机验证码登录页（`/login`）。
 *
 * 依据 10 文档：
 * - 区号 + 手机号、6 位验证码、必要协议勾选（未勾选不能提交，服务端也会拒绝）；
 * - 发送倒计时由服务端 `retry_after` 驱动，倒计时期间禁用重发并说明原因；
 * - `returnTo` 只允许本站相对路径，拒绝 `//evil.example` 与编码绕过；
 * - 文案明确工作人员不会索取验证码；注销、隐私与投诉保留公开联系路径，
 *   不把行使权利强制绑定短信登录。
 *
 * 该页不输出 OG 标签、不使用 `v-html`（路由已设置 noindex 与 no-store）。
 */
import { computed, onMounted, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import CountdownButton from "@/modules/identity/components/CountdownButton.vue";
import OtpInput from "@/modules/identity/components/OtpInput.vue";
import {
  DEFAULT_PRIVACY_VERSION,
  describeIdentityError,
  errorCodeOf,
  isMainlandPhone,
  isSixDigitCode,
  normalizePhoneForSubmit,
  recordConsent,
  safeReturnPath,
} from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { BrandMark } from "@/shared/ui/BrandMark";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const site = useSiteStore();

/** 首发基线为中国大陆；列表只放已确认支持的地区，不做假入口。 */
const DIAL_CODES = [
  { code: "+86", label: "中国大陆 +86" },
  { code: "+852", label: "中国香港 +852" },
  { code: "+853", label: "中国澳门 +853" },
  { code: "+886", label: "中国台湾 +886" },
] as const;

const dialCode = ref<string>("+86");
const phone = ref("");
const code = ref("");
const accepted = ref(false);
const acceptedError = ref("");
const phoneError = ref("");
const codeError = ref("");
const formError = ref("");
const formErrorCode = ref("");
const devHint = ref("");
const challengeId = ref("");
const sending = ref(false);
const verifying = ref(false);
const countdown = ref<{ start: (seconds: number) => void } | null>(null);
const otp = ref<{ focus: () => void; reset: () => void } | null>(null);

const returnTo = computed(() => safeReturnPath(route.query.returnTo, "/account"));
const termsVersion = computed(() => site.commissionPolicy.terms_version || "commission-1");

const canSubmit = computed(
  () => isMainlandPhone(phone.value) && isSixDigitCode(code.value) && accepted.value && !!challengeId.value,
);

/** 禁用提交的原因必须写在按钮旁（11 文档）。 */
const submitBlockedReason = computed(() => {
  if (!isMainlandPhone(phone.value)) return "请先填写 11 位中国大陆手机号。";
  if (!challengeId.value) return "请先获取短信验证码。";
  if (!isSixDigitCode(code.value)) return "请输入 6 位数字验证码。";
  if (!accepted.value) return "请先勾选同意《用户服务协议》与《隐私政策》。";
  return "";
});

onMounted(() => {
  void site.load();
});

// 用户修正输入后立即撤掉字段错误，避免旧提示一直挂着（11 文档：错误贴字段）。
watch(phone, () => {
  if (phoneError.value && isMainlandPhone(phone.value)) phoneError.value = "";
});
watch(code, () => {
  if (codeError.value && isSixDigitCode(code.value)) codeError.value = "";
});

async function sendCode(): Promise<void> {
  formError.value = "";
  formErrorCode.value = "";
  devHint.value = "";
  phoneError.value = "";
  // 手机号格式由字段级错误提示（11 文档：错误贴字段）。
  // 发送按钮保持可用，否则这条提示永远无法触达用户。
  if (!phone.value.trim()) {
    phoneError.value = "请先填写手机号，用于接收登录验证码。";
    return;
  }
  if (!isMainlandPhone(phone.value)) {
    phoneError.value = "手机号格式不正确：中国大陆号码为 1 开头的 11 位数字。";
    return;
  }
  sending.value = true;
  try {
    const challenge = await auth.requestSmsCode(normalizePhoneForSubmit(dialCode.value, phone.value), "login");
    challengeId.value = challenge.challenge_id;
    devHint.value = challenge.dev_hint ?? "";
    otp.value?.focus();
    countdown.value?.start(challenge.retry_after);
  } catch (caught) {
    const code0 = errorCodeOf(caught);
    formErrorCode.value = code0;
    formError.value = describeIdentityError(caught);
    // 限流时服务端会给 Retry-After 语义；这里按码分支，不解析 message。
    if (code0 === "SMS_RATE_LIMITED" || code0 === "RATE_LIMITED") {
      countdown.value?.start(60);
    }
  } finally {
    sending.value = false;
  }
}

async function submit(): Promise<void> {
  formError.value = "";
  formErrorCode.value = "";
  codeError.value = "";
  acceptedError.value = "";

  if (!isSixDigitCode(code.value)) {
    codeError.value = "验证码为 6 位数字，请检查是否漏输或包含空格。";
    return;
  }
  if (!accepted.value) {
    acceptedError.value = "需要先勾选同意《用户服务协议》与《隐私政策》才能登录。";
    return;
  }
  if (!challengeId.value) {
    formError.value = "验证码已失效，请重新获取。";
    return;
  }

  verifying.value = true;
  try {
    await auth.verifySms({
      challengeId: challengeId.value,
      phone: normalizePhoneForSubmit(dialCode.value, phone.value),
      code: code.value,
      termsVersion: termsVersion.value,
      privacyVersion: DEFAULT_PRIVACY_VERSION,
      accepted: true,
    });
    // 营销邮件默认不订阅（FR-018）：登录本身不写入任何营销同意。
    void recordConsent({
      purpose: "terms",
      document_version: termsVersion.value,
      accepted: true,
    }).catch(() => undefined);
    await router.replace(returnTo.value);
  } catch (caught) {
    const failure = errorCodeOf(caught);
    formErrorCode.value = failure;
    formError.value = describeIdentityError(caught);
    if (failure === "SMS_CODE_INVALID" || failure === "SMS_CODE_EXPIRED" || failure === "SMS_CHALLENGE_USED") {
      codeError.value = formError.value;
      code.value = "";
      otp.value?.reset();
      if (failure !== "SMS_CODE_INVALID") challengeId.value = "";
    }
  } finally {
    verifying.value = false;
  }
}
</script>

<template>
  <div class="container-page py-12 lg:py-16">
    <div class="mx-auto grid max-w-5xl gap-10 lg:grid-cols-[1fr_1fr]">
      <div>
        <div class="flex items-center gap-3">
          <BrandMark :size="44" decorative />
          <div>
            <p class="eyebrow">{{ site.brand.brand_name }}</p>
            <p class="text-sm text-ink-muted">客户账号登录</p>
          </div>
        </div>

        <h1 class="mt-8 font-display text-3xl leading-tight sm:text-4xl">手机号验证码登录</h1>
        <p class="mt-3 max-w-md text-sm leading-relaxed text-ink-muted">
          浏览不需要登录；提交询价、查看订单与进度时才需要。首次验证通过会按需创建账号。
        </p>

        <div class="mt-6 rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong">
          <p class="font-medium">工作人员不会向你索取验证码。</p>
          <p class="mt-1 text-xs leading-relaxed">
            任何人以「客服」「补单」「退款」为由索要验证码都是冒充。验证码只在 5 分钟内有效、
            只能使用一次；我们不会把它写进日志或回复消息。
          </p>
        </div>

        <SurfaceCard class="mt-6" as="section">
          <h2 class="font-display text-lg">登录遇到问题？</h2>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>
              注销账号、访问或更正资料、撤回展示授权等数据权利请求，不必先登录：
              <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
              （{{ site.brand.service_hours }}）。
            </li>
            <li>
              投诉与售后同样保留公开入口：
              <RouterLink class="link-underline" to="/contact">联系与反馈</RouterLink>，
              或查看
              <RouterLink class="link-underline" to="/legal/privacy">隐私政策与第三方清单</RouterLink>。
            </li>
            <li>员工使用独立后台入口与多因素验证，客户登录不会获得员工权限。</li>
          </ul>
        </SurfaceCard>
      </div>

      <SurfaceCard as="section" class="h-fit">
        <h2 class="font-display text-xl">使用短信验证码登录</h2>
        <p class="mt-2 text-xs text-ink-faint">
          未勾选协议不能提交，服务端也会再次拒绝；我们不会代替你勾选。
        </p>

        <form class="mt-6 space-y-5" novalidate @submit.prevent="submit">
          <FieldRow label="区号" for="login-dial" required>
            <select id="login-dial" v-model="dialCode" class="field-base" name="dial">
              <option v-for="item in DIAL_CODES" :key="item.code" :value="item.code">{{ item.label }}</option>
            </select>
          </FieldRow>

          <FieldRow
            label="手机号"
            for="login-phone"
            required
            :error="phoneError"
            hint="用于接收验证码与订单通知；不会出现在公开页面。"
          >
            <input
              id="login-phone"
              v-model.trim="phone"
              class="field-base tabular-nums"
              type="tel"
              inputmode="numeric"
              autocomplete="tel-national"
              name="phone"
              maxlength="11"
              placeholder="11 位手机号"
              :aria-invalid="phoneError ? 'true' : undefined"
            />
          </FieldRow>

          <div class="space-y-2">
            <OtpInput
              ref="otp"
              v-model="code"
              data-testid="otp-input"
              :error="codeError"
              :disabled="verifying"
              hint="点击「获取验证码」后，短信里的 6 位数字会出现在这里；支持整段粘贴。"
            />
            <CountdownButton ref="countdown" :loading="sending" @send="sendCode" />
          </div>

          <div class="space-y-2">
            <label class="flex items-start gap-3 text-sm text-ink-soft">
              <input
                v-model="accepted"
                type="checkbox"
                class="mt-1 size-4 shrink-0 accent-amber"
                name="accepted"
                :aria-invalid="acceptedError ? 'true' : undefined"
              />
              <span>
                我已阅读并同意
                <RouterLink class="link-underline" to="/legal/terms" target="_blank">《用户服务协议》</RouterLink>
                与
                <RouterLink class="link-underline" to="/legal/privacy" target="_blank">《隐私政策》</RouterLink>，
                知悉短信验证码只用于本次登录与敏感操作验证。
              </span>
            </label>
            <p v-if="acceptedError" class="text-xs font-medium text-wine" role="alert">{{ acceptedError }}</p>
          </div>

          <p
            v-if="formError"
            class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine"
            role="alert"
          >
            {{ formError }}
            <span v-if="formErrorCode" class="ml-2 font-mono text-xs text-wine/80">{{ formErrorCode }}</span>
          </p>

          <div class="flex flex-wrap items-center gap-3">
            <AppButton
              type="submit"
              size="lg"
              :loading="verifying"
              :disabled="!canSubmit"
              :disabled-reason="submitBlockedReason"
            >
              登录 / 注册
            </AppButton>
            <RouterLink class="link-underline text-sm text-ink-muted" :to="returnTo">
              先去目标页面（登录后可返回）
            </RouterLink>
          </div>

          <p class="text-xs text-ink-faint">
            登录后回到 <span class="font-mono">{{ returnTo }}</span
            >。返回目标只允许本站相对路径，`//evil.example` 这类外部地址与编码绕过写法一律忽略，
            不合法时回到「我的账户」。
          </p>
        </form>

        <!-- 开发提示：只在服务端返回 dev_hint 时显示，明确标注不是生产行为 -->
        <div
          v-if="devHint"
          data-testid="dev-hint"
          class="mt-6 rounded-sm border border-dashed border-line-strong bg-canvas-sunk px-3 py-2 text-xs text-ink-muted"
          role="status"
        >
          <div class="flex items-center gap-2">
            <StatusBadge tone="neutral" label="开发提示" />
            <span class="font-mono">{{ devHint }}</span>
          </div>
          <p class="mt-1 leading-relaxed">
            该提示只在开发/测试环境返回，用于本地联调；生产环境不存在万能码、测试号码白名单，
            也不会把验证码写入响应。
          </p>
        </div>
      </SurfaceCard>
    </div>
  </div>
</template>
