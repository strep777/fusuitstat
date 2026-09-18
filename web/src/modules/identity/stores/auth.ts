/**
 * 会话与偏好状态（Pinia）。
 *
 * 会话视图保存在前端，服务端是订单、库存、积分与权限的权威源（04 文档）。
 */

import { defineStore } from "pinia";
import { computed, ref } from "vue";

import { ApiError, api, clearCsrfToken, newIdempotencyKey, setCsrfToken } from "@/shared/lib/http";

export interface UserSummary {
  id: string;
  nickname: string;
  phone_masked: string;
  locale: string;
  currency: string;
  region: string;
  status: string;
  created_at?: string | null;
}

export interface MeResponse {
  user: UserSummary;
  permissions: string[];
  sensitive_verified: boolean;
  unread_notifications: number;
  pending_actions: Record<string, number>;
}

export interface SessionResponse {
  user: UserSummary;
  session_expires_at: string;
  idle_expires_at: string;
  csrf_token: string;
  is_new_user: boolean;
}

export interface SmsChallengeResponse {
  challenge_id: string;
  expires_in: number;
  retry_after: number;
  dev_hint?: string | null;
}

export const useAuthStore = defineStore("auth", () => {
  const user = ref<UserSummary | null>(null);
  const permissions = ref<string[]>([]);
  const unread = ref(0);
  const pendingActions = ref<Record<string, number>>({});
  const sensitiveVerified = ref(false);
  const loading = ref(false);
  const loaded = ref(false);
  const lastError = ref<{ code: string; message: string } | null>(null);

  const isAuthenticated = computed(() => user.value !== null);
  const displayName = computed(() => user.value?.nickname ?? "访客");
  const currency = computed<"CNY" | "USD">(() =>
    user.value?.currency === "USD" ? "USD" : "CNY",
  );
  const hasPendingActions = computed(
    () => Object.values(pendingActions.value).some((value) => value > 0),
  );

  async function loadMe(): Promise<MeResponse | null> {
    loading.value = true;
    try {
      const payload = await api.get<MeResponse>("/me");
      user.value = payload.user;
      permissions.value = payload.permissions;
      unread.value = payload.unread_notifications;
      pendingActions.value = payload.pending_actions;
      sensitiveVerified.value = payload.sensitive_verified;
      lastError.value = null;
      return payload;
    } catch (error) {
      if (error instanceof ApiError && error.needsLogin) {
        user.value = null;
        permissions.value = [];
        unread.value = 0;
        pendingActions.value = {};
        return null;
      }
      throw error;
    } finally {
      loading.value = false;
      loaded.value = true;
    }
  }

  async function requestSmsCode(phone: string, purpose = "login"): Promise<SmsChallengeResponse> {
    const payload = await api.post<SmsChallengeResponse>("/auth/sms/challenges", {
      phone,
      purpose,
    });
    return payload;
  }

  async function verifySms(input: {
    challengeId: string;
    phone: string;
    code: string;
    termsVersion: string;
    privacyVersion: string;
    accepted: boolean;
    /** 返回目标：只允许本站相对路径，服务端会再校验一次（10 文档）。 */
    returnTo?: string;
  }): Promise<SessionResponse> {
    const payload = await api.post<SessionResponse>("/auth/sms/verify", {
      challenge_id: input.challengeId,
      phone: input.phone,
      code: input.code,
      terms_version: input.termsVersion,
      privacy_version: input.privacyVersion,
      accepted: input.accepted,
      return_to: input.returnTo,
    });
    setCsrfToken(payload.csrf_token, 86400);
    user.value = payload.user;
    await loadMe();
    return payload;
  }

  /**
   * 更新昵称/头像。必须带 `version`（乐观锁）；409 `VERSION_CONFLICT`
   * 由调用方提示「刷新后重试」，不在这里静默重试。
   */
  async function updateProfile(input: {
    nickname?: string;
    locale?: string;
    currency?: string;
    avatarAssetId?: string;
    version: number;
  }): Promise<UserSummary> {
    const payload = await api.patch<UserSummary>("/me", {
      nickname: input.nickname,
      locale: input.locale,
      currency: input.currency,
      avatar_asset_id: input.avatarAssetId,
      version: input.version,
    });
    user.value = payload;
    return payload;
  }

  async function logout(): Promise<void> {
    try {
      await api.post("/auth/logout", undefined, { idempotencyKey: newIdempotencyKey() });
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
    } finally {
      user.value = null;
      permissions.value = [];
      unread.value = 0;
      pendingActions.value = {};
      clearCsrfToken();
    }
  }

  async function revokeOtherSessions(): Promise<number> {
    const payload = await api.post<{ revoked: number }>("/me/sessions/revoke-others");
    return payload.revoked;
  }

  async function markSensitiveVerified(): Promise<void> {
    await api.post("/me/sensitive-verify");
    sensitiveVerified.value = true;
  }

  function setUnread(value: number): void {
    unread.value = value;
  }

  return {
    user,
    permissions,
    unread,
    pendingActions,
    sensitiveVerified,
    loading,
    loaded,
    lastError,
    isAuthenticated,
    displayName,
    currency,
    hasPendingActions,
    loadMe,
    requestSmsCode,
    verifySms,
    updateProfile,
    logout,
    revokeOtherSessions,
    markSensitiveVerified,
    setUnread,
  };
});
