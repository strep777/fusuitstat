<template>
  <SurfaceCard>
    <h2>交易邮箱</h2>
    <p>报价、合同和订单进展通过邮件发送。请先验证邮箱。</p>
    <p v-if="loading" role="status">正在读取邮箱状态…</p>
    <p v-else-if="status?.verified">已验证：{{ status.email_masked }}</p>
    <p v-else>尚未绑定已验证邮箱。</p>
    <p v-if="error" role="alert">{{ error }}</p>
    <RouterLink v-if="expired" to="/login?return_to=/account/profile">重新登录</RouterLink>
    <form v-else @submit.prevent="send">
      <label for="transaction-email">邮箱地址</label>
      <input id="transaction-email" v-model="email" type="email" maxlength="254" required autocomplete="email" :disabled="busy" />
      <AppButton type="submit" :disabled="busy || loading || !email.trim()">发送验证邮件</AppButton>
      <p v-if="!email.trim()">填写邮箱后可发送验证码。</p>
      <p v-if="busy" role="status">正在提交，请稍候。</p>
    </form>
    <form v-if="sent && !expired" @submit.prevent="confirm">
      <label for="email-code">邮件中的六位验证码</label>
      <input id="email-code" v-model="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{6}" maxlength="6" required :disabled="busy" />
      <AppButton type="submit" :disabled="busy || code.length !== 6">验证邮箱</AppButton>
      <p v-if="code.length !== 6">请输入完整的六位验证码，验证码十分钟内有效。</p>
    </form>
    <p v-if="saved" role="status">邮箱验证成功，待发交易邮件已重新排队。</p>
  </SurfaceCard>
</template>

<script setup lang="ts">
import { onMounted, ref, shallowRef } from "vue";
import { RouterLink } from "vue-router";
import { apiRequest, ApiError } from "@/shared/lib/http";
import { AppButton, SurfaceCard } from "@/shared/ui/base";

interface EmailStatus { verified: boolean; email_masked: string | null }
const status = shallowRef<EmailStatus | null>(null);
const email = ref("");
const code = ref("");
const loading = ref(true);
const busy = ref(false);
const error = ref("");
const expired = ref(false);
const sent = ref(false);
const saved = ref(false);

function showError(caught: unknown): void {
  expired.value = caught instanceof ApiError && ["AUTH_REQUIRED", "SESSION_EXPIRED"].includes(caught.code);
  error.value = expired.value ? "登录已过期，请重新登录。" : caught instanceof ApiError ? caught.message : "暂时无法完成操作，请稍后重试。";
}

onMounted(async () => {
  try { status.value = await apiRequest<EmailStatus>("/me/email"); }
  catch (caught) { showError(caught); }
  finally { loading.value = false; }
});

async function send(): Promise<void> {
  busy.value = true; error.value = ""; saved.value = false;
  try {
    await apiRequest("/me/email/verification", { method: "POST", body: { email: email.value.trim() } });
    sent.value = true; code.value = "";
  } catch (caught) { showError(caught); }
  finally { busy.value = false; }
}

async function confirm(): Promise<void> {
  busy.value = true; error.value = "";
  try {
    status.value = await apiRequest<EmailStatus>("/me/email/confirm", { method: "POST", body: { code: code.value } });
    saved.value = true; sent.value = false; code.value = "";
  } catch (caught) { showError(caught); }
  finally { busy.value = false; }
}
</script>

<style scoped>
input { display: block; width: 100%; max-width: 28rem; margin: .5rem 0 1rem; padding: .75rem; border: 1px solid currentColor; border-radius: .5rem; background: transparent; }
form { margin-top: 1rem; }
</style>
