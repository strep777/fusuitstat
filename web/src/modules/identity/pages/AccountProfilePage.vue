

<template>
  <EmailVerification />
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="crumbs" />

    <SectionHeading
      class="mt-4"
      :level="1"
      eyebrow="Profile"
      title="个人资料"
      description="只收集履约与合作必需的信息。昵称用于订单与沟通，头像可留空。"
    />

    <div v-if="loading" class="mt-8">
      <SkeletonBlock :lines="5" aria-label="正在读取个人资料" />
    </div>

    <EmptyState
      v-else-if="sessionExpired"
      class="mt-8"
      tone="error"
      title="登录状态已过期"
      description="为保护你的资料，请重新登录后再继续。已填写的内容不会被发送到任何第三方。"
    >
      <AppButton size="sm" to="/login?returnTo=/account/profile">重新登录</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="forbidden"
      class="mt-8"
      tone="forbidden"
      title="当前账号无权查看该资料"
      description="请确认登录的账号；如需核对账号归属，请通过公开客服邮箱联系。"
    >
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <EmptyState
      v-else-if="loadError"
      class="mt-8"
      tone="error"
      title="资料加载失败"
      :description="`${loadError} 网络恢复后可以重试。`"
    >
      <AppButton size="sm" @click="load()">重新加载</AppButton>
    </EmptyState>

    <div v-else class="mt-8 grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
      <SurfaceCard as="section">
        <h2 class="font-display text-xl">昵称与头像</h2>
        <p class="mt-2 text-xs text-ink-faint">
          保存时会带上版本号做乐观锁校验：如果资料已在别处修改，我们会提示你刷新后重试，而不是静默覆盖。
        </p>

        <form class="mt-6 space-y-5" novalidate @submit.prevent="save">
          <FieldRow
            label="昵称"
            for="profile-nickname"
            required
            :error="validationError"
            hint="1–40 个字符，会显示在报价、订单与通知里。"
          >
            <input
              id="profile-nickname"
              v-model="nickname"
              class="field-base"
              type="text"
              name="nickname"
              maxlength="40"
              autocomplete="nickname"
              :aria-invalid="validationError ? 'true' : undefined"
            />
          </FieldRow>

          <FieldRow
            label="头像素材 ID"
            for="profile-avatar"
            hint="填写后台分配的头像素材 ID；留空使用工作室默认标志。头像本身在素材后台审核后生效。"
          >
            <input
              id="profile-avatar"
              v-model="avatarAssetId"
              class="field-base font-mono"
              type="text"
              name="avatar"
              maxlength="48"
              placeholder="asset_xxx（可留空）"
            />
          </FieldRow>

          <p v-if="saveError" class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
            {{ saveError }}
          </p>
          <p v-else-if="savedAt" class="text-sm text-jade" role="status">
            已保存（{{ formatDateTime(savedAt) }}）。
          </p>

          <div class="flex flex-wrap items-center gap-3">
            <AppButton
              type="submit"
              :loading="saving"
              :disabled="!dirty"
              :disabled-reason="dirty ? '' : '没有需要保存的修改。'"
            >
              保存修改
            </AppButton>
            <AppButton variant="secondary" :disabled="!dirty" disabled-reason="没有需要撤销的修改。" @click="reset()">
              撤销修改
            </AppButton>
            <span class="text-xs text-ink-faint">
              状态：{{ saving ? "保存中" : dirty ? "有未保存修改" : "已同步" }}
            </span>
          </div>
        </form>
      </SurfaceCard>

      <div class="space-y-6">
        <SurfaceCard as="section">
          <h2 class="font-display text-lg">我们刻意不收集的信息</h2>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>不要求生日、性别、身份证号或人脸信息：这些不是完成委托与发货的必要条件。</li>
            <li>浏览不需要登录；询价阶段不收身份证与完整地址，量体数据只在接单后按需收集。</li>
            <li>量体与身体数据属于高敏感内部资料，单独同意、单独授权访问，并设更短保留期。</li>
            <li>未成年人的资料按监护人确认流程处理，不用一个年龄勾选框代替义务。</li>
          </ul>
          <p class="mt-3 text-xs text-ink-faint">
            需要访问、更正或删除资料时，可通过
            <RouterLink class="link-underline" to="/contact">联系与反馈</RouterLink>
            或
            <a class="link-underline" :href="`mailto:${site.brand.support_email}`">{{ site.brand.support_email }}</a>
            提出，不必先登录。
          </p>
        </SurfaceCard>

        <SurfaceCard as="section">
          <h2 class="font-display text-lg">头像预览</h2>
          <div class="mt-3 flex items-center gap-4">
            <BrandMark :size="64" decorative />
            <div class="text-sm text-ink-muted">
              <p>{{ nickname || savedNickname || "未设置昵称" }}</p>
              <p class="mt-1 text-xs text-ink-faint">
                {{ avatarAssetId ? `头像素材：${avatarAssetId}` : "当前使用工作室默认标志" }}
              </p>
            </div>
          </div>
        </SurfaceCard>

        <SurfaceCard as="section">
          <h2 class="font-display text-lg">同意与订阅</h2>
          <p class="mt-2 text-xs text-ink-faint">
            必要同意（服务协议、隐私政策）与可选同意分开记录。营销邮件默认不订阅，可随时在这里退订，
            不影响订单与售后通知。
          </p>

          <div class="mt-4">
            <SkeletonBlock v-if="consentsLoading" :lines="3" aria-label="正在读取同意记录" />
            <p v-else-if="consentsError" class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine" role="alert">
              {{ consentsError }}
            </p>
            <ul v-else class="space-y-3">
              <li
                v-for="purpose in OPTIONAL_CONSENT_PURPOSES"
                :key="purpose"
                class="flex items-start justify-between gap-4 border-b border-line pb-3 last:border-b-0 last:pb-0"
              >
                <div>
                  <p class="text-sm font-medium">{{ CONSENT_PURPOSE_LABEL[purpose] }}</p>
                  <p class="mt-1 text-xs text-ink-faint">
                    <template v-if="consentState[purpose]">
                      已同意 · 版本 {{ consentState[purpose]?.document_version }} ·
                      {{ formatDateTime(consentState[purpose]?.accepted_at ?? null) }}
                    </template>
                    <template v-else>未同意（默认状态）</template>
                  </p>
                </div>
                <AppButton
                  size="sm"
                  :variant="isConsentActive(purpose) ? 'secondary' : 'primary'"
                  :loading="consentBusy === purpose"
                  @click="toggleConsent(purpose, !isConsentActive(purpose))"
                >
                  {{ isConsentActive(purpose) ? "撤回" : "同意" }}
                </AppButton>
              </li>
            </ul>
          </div>

          <div class="mt-5">
            <StatusBadge tone="neutral" label="展示授权可单独撤回" />
            <p class="mt-2 text-xs text-ink-faint">
              撤回展示授权不影响已交付商品的正常使用，也不影响你在订单与售后中的权利。
            </p>
          </div>
        </SurfaceCard>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
/**
 * 个人资料（`/account/profile`）。
 *
 * 范围（03 文档）：昵称、头像；**非履约必要不收生日性别**。
 * 表单状态：dirty / saving / saved / validation-error / session-expired。
 * `PATCH /me` 必须带 `version`；409 `VERSION_CONFLICT` 时提示刷新后重试。
 */
import { computed, onMounted, ref } from "vue";
import EmailVerification from "@/modules/identity/components/EmailVerification.vue";

import {
  CONSENT_PURPOSE_LABEL,
  DEFAULT_PRIVACY_VERSION,
  OPTIONAL_CONSENT_PURPOSES,
  describeIdentityError,
  errorCodeOf,
  fetchConsents,
  isSessionExpired,
  recordConsent,
  type ConsentPurpose,
  type ConsentRecord,
} from "@/modules/identity/api/identity";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { BrandMark } from "@/shared/ui/BrandMark";
import { formatDateTime } from "@/shared/lib/format";
import { ApiError } from "@/shared/lib/http";

const auth = useAuthStore();
const site = useSiteStore();

const loading = ref(true);
const loadError = ref("");
const forbidden = ref(false);
const sessionExpired = ref(false);

const nickname = ref("");
const savedNickname = ref("");
const avatarAssetId = ref("");
const savedAvatarAssetId = ref("");
const version = ref(1);

const validationError = ref("");
const saveError = ref("");
const savedAt = ref<string | null>(null);
const saving = ref(false);

const consents = ref<ConsentRecord[]>([]);
const consentsLoading = ref(true);
const consentsError = ref("");
const consentBusy = ref<ConsentPurpose | null>(null);

const crumbs = [
  { label: "首页", to: "/" },
  { label: "我的账户", to: "/account" },
  { label: "个人资料" },
];

const dirty = computed(
  () => nickname.value !== savedNickname.value || avatarAssetId.value !== savedAvatarAssetId.value,
);

const nicknameError = computed(() => {
  const value = nickname.value.trim();
  if (!value) return "昵称不能为空。";
  if (value.length > 40) return "昵称最多 40 个字符。";
  return "";
});

const consentState = computed<Record<string, ConsentRecord | undefined>>(() => {
  const map: Record<string, ConsentRecord | undefined> = {};
  for (const row of consents.value) {
    if (row.revoked_at) continue;
    if (!map[row.purpose]) map[row.purpose] = row;
  }
  return map;
});

function isConsentActive(purpose: ConsentPurpose): boolean {
  return Boolean(consentState.value[purpose]);
}

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = "";
  forbidden.value = false;
  sessionExpired.value = false;
  try {
    const payload = await auth.loadMe();
    if (!payload) {
      sessionExpired.value = true;
      return;
    }
    nickname.value = payload.user.nickname;
    savedNickname.value = payload.user.nickname;
    avatarAssetId.value = "";
    savedAvatarAssetId.value = "";
    savedAt.value = null;
  } catch (caught) {
    loadError.value = describeIdentityError(caught);
    forbidden.value = errorCodeOf(caught) === "FORBIDDEN" || (caught instanceof ApiError && caught.status === 403);
    sessionExpired.value = isSessionExpired(caught);
  } finally {
    loading.value = false;
  }
}

async function loadConsents(): Promise<void> {
  consentsLoading.value = true;
  consentsError.value = "";
  try {
    consents.value = await fetchConsents();
  } catch (caught) {
    consents.value = [];
    consentsError.value = describeIdentityError(caught);
  } finally {
    consentsLoading.value = false;
  }
}

async function save(): Promise<void> {
  saveError.value = "";
  validationError.value = "";
  savedAt.value = null;
  if (nicknameError.value) {
    validationError.value = nicknameError.value;
    return;
  }
  saving.value = true;
  try {
    await auth.updateProfile({
      nickname: nickname.value.trim(),
      avatarAssetId: avatarAssetId.value.trim() || undefined,
      version: version.value,
    });
    savedNickname.value = nickname.value.trim();
    savedAvatarAssetId.value = avatarAssetId.value.trim();
    version.value += 1;
    savedAt.value = new Date().toISOString();
  } catch (caught) {
    const code = errorCodeOf(caught);
    if (code === "VERSION_CONFLICT") {
      saveError.value = "资料已在其他页面被更新，请刷新后重新提交，避免覆盖对方的修改。";
      version.value += 1;
    } else if (isSessionExpired(caught)) {
      sessionExpired.value = true;
      saveError.value = "登录状态已过期，请重新登录后再保存。";
    } else {
      saveError.value = describeIdentityError(caught);
    }
  } finally {
    saving.value = false;
  }
}

function reset(): void {
  nickname.value = savedNickname.value;
  avatarAssetId.value = savedAvatarAssetId.value;
  validationError.value = "";
  saveError.value = "";
}

async function toggleConsent(purpose: ConsentPurpose, accepted: boolean): Promise<void> {
  consentBusy.value = purpose;
  consentsError.value = "";
  try {
    await recordConsent({
      purpose,
      document_version: purpose === "privacy" ? DEFAULT_PRIVACY_VERSION : site.commissionPolicy.terms_version,
      accepted,
    });
    await loadConsents();
  } catch (caught) {
    consentsError.value = describeIdentityError(caught);
  } finally {
    consentBusy.value = null;
  }
}

onMounted(() => {
  void site.load();
  void Promise.all([load(), loadConsents()]);
});
</script>
