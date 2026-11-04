<script setup lang="ts">
/**
 * 员工登录：口令 + TOTP/恢复码两步；首次登录先登记 MFA。
 *
 * 约束（03/10 文档）：无公开注册、无默认密码；MFA 重置需身份核验与审计。
 * otpauth URI 与恢复码**只显示一次**，界面必须写明；这里不显示任何密钥以外的账号信息。
 */
import { computed, onMounted, ref } from "vue";
import { useRoute, useRouter } from "vue-router";

import { safeReturnTo } from "@/app/admin-router";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { ApiError } from "@/shared/lib/http";
import { BrandMark } from "@/shared/ui/BrandMark";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";

const auth = useAdminAuthStore();
const route = useRoute();
const router = useRouter();

const loginName = ref("");
const password = ref("");
const code = ref("");
const codesSaved = ref(false);
const errorText = ref("");
const codeError = ref("");
const copied = ref("");

const returnTo = computed(() => safeReturnTo(route.query.returnTo));
const enroll = computed(() => auth.enrollment);
const challengeFailed = computed(() => auth.sessionExpired);

onMounted(() => {
  if (challengeFailed.value) {
    errorText.value = "上一次后台会话已过期或被撤销，请重新输入口令并完成 MFA。";
  }
});

async function submitCredential(): Promise<void> {
  errorText.value = "";
  if (!loginName.value.trim() || !password.value) {
    errorText.value = "请填写员工登录名与口令。";
    return;
  }
  try {
    await auth.login(loginName.value.trim(), password.value);
  } catch (error) {
    errorText.value = error instanceof ApiError ? error.message : "登录失败，请稍后重试。";
  }
}

async function submitCode(): Promise<void> {
  codeError.value = "";
  if (code.value.trim().length < 6) {
    codeError.value = "请输入 6 位动态验证码，或使用一枚恢复码（恢复码较长，可直接粘贴）。";
    return;
  }
  try {
    await auth.verifyMfa(code.value.trim());
    await router.replace(returnTo.value);
  } catch (error) {
    codeError.value =
      error instanceof ApiError
        ? `${error.message}（错误码 ${error.code}）`
        : "验证失败，请重新输入当前动态验证码。";
  }
}

async function copy(text: string, label: string): Promise<void> {
  try {
    await navigator.clipboard.writeText(text);
    copied.value = label;
  } catch {
    copied.value = "";
  }
}

function restart(): void {
  code.value = "";
  codeError.value = "";
  password.value = "";
  codesSaved.value = false;
  auth.resetChallenge();
}
</script>

<template>
  <div class="grid min-h-screen bg-canvas lg:grid-cols-[0.85fr_1.15fr]">
    <!-- 品牌侧板：安全事实，不放营销空话 -->
    <aside class="relative hidden flex-col justify-between overflow-hidden border-r border-line bg-[#f1e7db] p-10 text-ink lg:flex">
      <div class="pointer-events-none absolute inset-0 bg-dots opacity-40" aria-hidden="true" />
      <div class="relative flex items-center gap-3">
        <BrandMark :size="40" decorative />
        <div>
          <p class="font-display text-base font-medium">弈星纪元</p>
          <p class="font-mono text-[9px] uppercase tracking-[0.2em] text-ink-faint">Studio Console</p>
        </div>
      </div>

      <div class="relative">
        <p class="text-[10px] uppercase tracking-[0.22em] text-brand-strong">Staff only / 员工专用</p>
        <p class="mt-5 font-display text-[clamp(3rem,5.2vw,4.8rem)] font-medium leading-[0.96] tracking-[-0.05em]">
          STUDIO<br />CONSOLE
        </p>
        <p class="mt-5 max-w-sm text-sm leading-relaxed text-ink-muted">
          独立员工账号、强制 MFA、逐动作权限校验。后台页面 noindex，不进入搜索索引，也不预渲染个人资料。
        </p>
      </div>

      <div class="relative grid gap-2">
        <div
          v-for="(item, index) in ['口令 + MFA 两步验证', '写操作全量审计', '会话到期自动失效']"
          :key="item"
          class="flex items-center gap-3 rounded-full border border-line bg-surface/65 px-4 py-2.5 text-xs text-ink-muted"
        >
          <span class="text-[9px] text-brand">{{ String(index + 1).padStart(2, "0") }}</span>
          {{ item }}
        </div>
      </div>
    </aside>

    <main class="flex flex-col justify-center px-4 py-10 sm:px-8 lg:px-14">
      <div class="mx-auto w-full max-w-xl">
        <div class="flex items-center gap-3 lg:hidden">
          <BrandMark :size="44" decorative />
          <div>
            <p class="font-mono text-[10px] uppercase tracking-[0.18em] text-ink-muted">Studio Console</p>
            <p class="font-display text-lg font-bold">弈星纪元 · 运营后台</p>
          </div>
        </div>

        <p class="mt-8 text-[10px] font-medium uppercase tracking-[0.2em] text-brand-strong lg:mt-0">
          01 / Sign in
        </p>
        <h1 class="mt-2 font-display text-4xl font-medium tracking-[-0.03em] sm:text-5xl">员工登录</h1>
        <p class="mt-3 max-w-lg text-sm leading-relaxed text-ink-muted">
          后台不继承前台客户登录状态，也没有公开注册入口。登录后所有写操作按权限码逐次校验并记录审计。
        </p>

        <p
          v-if="errorText"
          class="mt-6 rounded-xl border border-wine/30 bg-wine-soft px-4 py-3 text-sm text-wine"
          role="alert"
        >
          {{ errorText }}
        </p>

        <!-- 第一步：口令 -->
        <SurfaceCard class="mt-6">
          <div class="flex items-center justify-between gap-3">
            <h2 class="font-display text-xl font-bold">第一步 · 口令</h2>
            <StatusBadge
              :tone="auth.step === 'credential' ? 'amber' : 'jade'"
              :label="auth.step === 'credential' ? '进行中' : '已完成'"
            />
          </div>

          <form class="mt-5 space-y-4" @submit.prevent="submitCredential">
            <FieldRow label="员工登录名" required for="admin-login-name">
              <input
                id="admin-login-name"
                v-model="loginName"
                type="text"
                class="field-base"
                autocomplete="username"
                :disabled="auth.step !== 'credential'"
              />
            </FieldRow>
            <FieldRow label="口令" required for="admin-login-password" hint="口令由管理员设置；忘记口令需联系管理员重置并审计。">
              <input
                id="admin-login-password"
                v-model="password"
                type="password"
                class="field-base"
                autocomplete="current-password"
                :disabled="auth.step !== 'credential'"
              />
            </FieldRow>
            <AppButton
              type="submit"
              :loading="auth.loading"
              :disabled="auth.step !== 'credential'"
              :disabled-reason="auth.step !== 'credential' ? '口令已通过，请继续完成第二步验证' : ''"
            >
              继续验证
            </AppButton>
          </form>
        </SurfaceCard>

        <!-- 首次登录：登记 MFA -->
        <SurfaceCard v-if="auth.step === 'enroll' && enroll" class="mt-6">
          <div class="flex items-center justify-between gap-3">
            <h2 class="font-display text-xl font-bold">第二步 · 首次登记 MFA</h2>
            <StatusBadge tone="wine" label="只显示一次" />
          </div>

          <p class="mt-4 border-2 border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
            下面的登记信息与恢复码<strong>只在这一次显示</strong>。关闭或刷新本页后无法再次查看；
            恢复码丢失时，MFA 重置必须经过身份核验并留下审计记录。
          </p>

          <div class="mt-5 space-y-5">
            <div>
              <h3 class="text-sm font-bold text-ink-soft">1. 把下面的 otpauth URI 录入验证器</h3>
              <p class="mt-1 text-xs text-ink-faint">
                在验证器 App 里选择「手动输入密钥」，粘贴下面整行 URI 即可；不要截图外发。
              </p>
              <pre
                class="mt-2 max-h-32 overflow-auto border-2 border-ink bg-canvas-sunk p-3 font-mono text-xs break-all whitespace-pre-wrap"
                data-testid="otpauth-uri"
              >{{ enroll.otpauth_uri }}</pre>
              <div class="mt-2 flex flex-wrap items-center gap-3">
                <AppButton size="sm" variant="secondary" @click="copy(enroll.otpauth_uri, 'otpauth')">
                  复制 otpauth URI
                </AppButton>
                <span v-if="copied === 'otpauth'" class="text-xs text-jade">已复制到剪贴板</span>
              </div>
            </div>

            <div>
              <h3 class="text-sm font-bold text-ink-soft">2. 保存恢复码（每枚只能用一次）</h3>
              <ul class="mt-2 grid gap-2 sm:grid-cols-2" data-testid="recovery-codes">
                <li
                  v-for="item in enroll.recovery_codes"
                  :key="item"
                  class="border-2 border-ink bg-surface-muted px-3 py-2 font-mono text-xs"
                >
                  {{ item }}
                </li>
              </ul>
              <div class="mt-2 flex flex-wrap items-center gap-3">
                <AppButton
                  size="sm"
                  variant="secondary"
                  @click="copy(enroll.recovery_codes.join('\n'), 'codes')"
                >
                  复制全部恢复码
                </AppButton>
                <span v-if="copied === 'codes'" class="text-xs text-jade">已复制到剪贴板</span>
              </div>
            </div>

            <label class="flex items-start gap-3 text-sm">
              <input v-model="codesSaved" type="checkbox" class="mt-1 size-5" />
              <span>我已把恢复码保存在安全的地方，也知道它们只显示这一次。</span>
            </label>

            <AppButton
              :disabled="!codesSaved"
              :disabled-reason="codesSaved ? '' : '请先确认已保存恢复码'"
              @click="auth.acknowledgeEnrollment()"
            >
              已保存，继续验证
            </AppButton>
          </div>
        </SurfaceCard>

        <!-- 第二步：动态验证码或恢复码 -->
        <SurfaceCard v-if="auth.step === 'verify'" class="mt-6">
          <div class="flex items-center justify-between gap-3">
            <h2 class="font-display text-xl font-bold">第二步 · 动态验证码</h2>
            <StatusBadge tone="amber" label="进行中" />
          </div>
          <p class="mt-3 text-sm text-ink-muted">
            输入验证器当前显示的 6 位数字；如果验证器不可用，可以粘贴一枚恢复码代替。
          </p>

          <form class="mt-5 space-y-4" @submit.prevent="submitCode">
            <FieldRow
              label="动态验证码或恢复码"
              required
              for="admin-mfa-code"
              :error="codeError"
              hint="验证码一次性使用；失败次数过多会触发限流，请等待后再试。"
            >
              <input
                id="admin-mfa-code"
                v-model="code"
                type="text"
                inputmode="numeric"
                autocomplete="one-time-code"
                class="field-base font-mono tracking-widest"
                maxlength="32"
              />
            </FieldRow>
            <div class="flex flex-wrap items-center gap-3">
              <AppButton type="submit" :loading="auth.loading">登录后台</AppButton>
              <AppButton variant="quiet" size="sm" @click="restart">重新输入口令</AppButton>
            </div>
          </form>
        </SurfaceCard>

        <p class="mt-8 border-t-2 border-ink/15 pt-4 text-xs leading-relaxed text-ink-faint">
          安全提示：不要把验证码、恢复码或 otpauth URI 发给任何人。没有某个模块的权限码时，
          导航会隐藏入口并列出缺少的权限码；服务端仍会逐动作校验权限。
        </p>
      </div>
    </main>
  </div>
</template>
