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
  isSixDigitCode,
  normalizePhoneForSubmit,
  phoneFormatError,
  recordConsent,
  safeReturnPath,
} from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { useI18n } from "@/shared/lib/i18n";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { BrandMark } from "@/shared/ui/BrandMark";

const route = useRoute();
const router = useRouter();
const auth = useAuthStore();
const site = useSiteStore();
const { t } = useI18n();

/**
 * 区号列表必须与服务端白名单（`SMS_REGIONS`，默认 CN,US）一致：
 * C02/C24 首发中国大陆 + 美国，其他地区在没有通道与合规确认前不做假入口。
 */
const DIAL_CODES = computed(() => [
  { code: "+86", label: t("login.dialCn") },
  { code: "+1", label: t("login.dialUs") },
]);

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
  () =>
    phoneFormatError(normalizePhoneForSubmit(dialCode.value, phone.value)) === "" &&
    isSixDigitCode(code.value) &&
    accepted.value &&
    !!challengeId.value,
);

/** 禁用提交的原因必须写在按钮旁（11 文档）。 */
const submitBlockedReason = computed(() => {
  if (!phone.value.trim()) return t("identity.phoneEmpty");
  if (phoneFormatError(normalizePhoneForSubmit(dialCode.value, phone.value))) {
    return t("identity.phoneInvalid");
  }
  if (!challengeId.value) return t("identity.challengeMissing");
  if (!isSixDigitCode(code.value)) return t("identity.codeInvalid");
  if (!accepted.value) return t("identity.agreementRequired");
  return "";
});

onMounted(() => {
  void site.load();
});

// 用户修正输入后立即撤掉字段错误，避免旧提示一直挂着（11 文档：错误贴字段）。
watch(phone, () => {
  if (phoneError.value && !phoneFormatError(normalizePhoneForSubmit(dialCode.value, phone.value))) {
    phoneError.value = "";
  }
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
    phoneError.value = t("identity.phoneRequiredToSend");
    return;
  }
  // C02/C24：中国大陆与美国号码都接受；服务端按 E.164 归一化并做区号白名单校验。
  const formatError = phoneFormatError(normalizePhoneForSubmit(dialCode.value, phone.value));
  if (formatError) {
    phoneError.value = formatError;
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
    codeError.value = t("identity.codeInvalidDetail");
    return;
  }
  if (!accepted.value) {
    acceptedError.value = t("identity.agreementRequiredSubmit");
    return;
  }
  if (!challengeId.value) {
    formError.value = t("identity.challengeUsed");
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
            <p class="text-sm text-ink-muted">{{ t("login.eyebrow") }}</p>
          </div>
        </div>

        <h1 class="mt-8 font-display text-3xl leading-tight sm:text-4xl">{{ t("login.title") }}</h1>
        <p class="mt-3 max-w-md text-sm leading-relaxed text-ink-muted">
          {{ t("login.intro") }}
        </p>

        <div class="mt-6 rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong">
          <p class="font-medium">{{ t("login.securityTitle") }}</p>
          <p class="mt-1 text-xs leading-relaxed">
            {{ t("login.securityBody") }}
          </p>
        </div>

        <SurfaceCard class="mt-6" as="section">
          <h2 class="font-display text-lg">{{ t("login.helpTitle") }}</h2>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>
              {{ t("login.helpRights") }}
              <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
              （{{ site.brand.service_hours }}）。
            </li>
            <li>
              {{ t("login.helpComplaintLead") }}
              <RouterLink class="link-underline" to="/contact">{{ t("login.helpContact") }}</RouterLink>{{ t("login.helpOr") }}
              <RouterLink class="link-underline" to="/legal/privacy">{{ t("login.helpPrivacy") }}</RouterLink>。
            </li>
            <li>{{ t("login.helpStaff") }}</li>
          </ul>
        </SurfaceCard>
      </div>

      <SurfaceCard as="section" class="h-fit">
        <h2 class="font-display text-xl">{{ t("login.formTitle") }}</h2>
        <p class="mt-2 text-xs text-ink-faint">
          {{ t("login.formHint") }}
        </p>

        <form class="mt-6 space-y-5" novalidate @submit.prevent="submit">
          <FieldRow :label="t('login.dialLabel')" for="login-dial" required>
            <select id="login-dial" v-model="dialCode" class="field-base" name="dial">
              <option v-for="item in DIAL_CODES" :key="item.code" :value="item.code">{{ item.label }}</option>
            </select>
          </FieldRow>

          <FieldRow
            :label="t('login.phoneLabel')"
            for="login-phone"
            required
            :error="phoneError"
            :hint="t('login.phoneHint')"
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
              :placeholder="t('login.phonePlaceholder')"
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
              :hint="t('login.otpHint')"
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
                {{ t("login.agreementLead") }}
                <RouterLink class="link-underline" to="/legal/terms" target="_blank">{{ t("login.terms") }}</RouterLink>
                {{ t("login.agreementAnd") }}
                <RouterLink class="link-underline" to="/legal/privacy" target="_blank">{{ t("login.privacy") }}</RouterLink>{{ t("login.agreementTail") }}
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
              {{ t("login.submit") }}
            </AppButton>
            <RouterLink class="link-underline text-sm text-ink-muted" :to="returnTo">
              {{ t("login.goToTarget") }}
            </RouterLink>
          </div>

          <p class="text-xs text-ink-faint">
            {{ t("login.returnLead") }} <span class="font-mono">{{ returnTo }}</span
            >{{ t("login.returnNote") }}
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
            <StatusBadge tone="neutral" :label="t('login.devHintLabel')" />
            <span class="font-mono">{{ devHint }}</span>
          </div>
          <p class="mt-1 leading-relaxed">
            {{ t("login.devHintNote") }}
          </p>
        </div>
      </SurfaceCard>
    </div>
  </div>
</template>
