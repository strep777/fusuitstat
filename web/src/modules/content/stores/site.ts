/**
 * 站点级状态：公开配置、功能开关、导航接单状态与通知条。
 * 服务端是权威源，这里只缓存展示所需的最小数据。
 */

import { defineStore } from "pinia";
import { computed, ref } from "vue";

import {
  fetchPublicSettings,
  type BrandProfile,
  type CommercePolicy,
  type CommissionPolicy,
  type LoyaltyRules,
  type PublicSettings,
  type ResolvedBrandProfile,
  type ResolvedCommercePolicy,
  type SocialLink,
} from "@/modules/content/api/content";
import { resolveText } from "@/shared/lib/i18n";
import { usePreferencesStore } from "@/shared/stores/preferences";

export interface ScheduleSummary {
  id: string;
  title: string;
  mode: string;
  status: "open" | "upcoming" | "closed";
  start_at: string;
  end_at: string;
  capacity: number;
  remaining: number;
  timezone: string;
}

const FALLBACK_PROFILE: BrandProfile = {
  brand_name: "弈星纪元兽装工作室",
  legal_entity: "弈星纪元（上海）文化发展有限公司",
  domain: "starium.cn",
  support_email: "support@starium.cn",
  service_hours: "周一至周五 10:00–19:00（Asia/Shanghai）",
  response_sla: { inquiry_workdays: 3, feedback_workdays: 7 },
};

export const useSiteStore = defineStore("site", () => {
  const preferences = usePreferencesStore();
  const settings = ref<PublicSettings>({});
  const loaded = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const commissionOpen = ref<boolean | null>(null);
  const scheduleSummary = ref<ScheduleSummary | null>(null);

  /** 运营文案在这里**统一按当前语言解析**：模板只拿到一句话，不会渲染出 `[object Object]`。 */
  const brand = computed<ResolvedBrandProfile>(() => {
    const raw: BrandProfile = settings.value["brand.profile"] ?? FALLBACK_PROFILE;
    return {
      brand_name: resolveText(raw.brand_name, preferences.locale),
      legal_entity: resolveText(raw.legal_entity, preferences.locale),
      domain: raw.domain,
      support_email: raw.support_email,
      service_hours: resolveText(raw.service_hours, preferences.locale),
      response_sla: raw.response_sla ?? { inquiry_workdays: 3, feedback_workdays: 7 },
    };
  });
  const socialLinks = computed<SocialLink[]>(() =>
    (settings.value["brand.social_links"] ?? []).filter((link) => link.url.trim().length > 0),
  );
  const commissionPolicy = computed<CommissionPolicy>(
    () =>
      settings.value["commission.policy"] ?? {
        prepayment_percent: 50,
        quote_valid_days: 7,
        slot_hold_minutes: 30,
        balance_due_days: 7,
        free_revision_count: 2,
        terms_version: "commission-1",
      },
  );
  const loyaltyRules = computed<LoyaltyRules>(
    () =>
      settings.value["loyalty.rules"] ?? {
        points_name: "星绒币",
        denominator_yuan: 5,
        release_delay_days: 7,
        shipping_excluded: true,
        feedback_rewards: { minor: 5, normal: 20, major: 50 },
        feedback_daily_reward_limit: 1,
        redemption_enabled: false,
      },
  );
  const commercePolicy = computed<ResolvedCommercePolicy>(() => {
    const raw: CommercePolicy =
      settings.value["commerce.policy"] ?? {
        returns_reference: "参照淘宝 / 闲鱼平台规则执行",
        cross_border_note: "跨境订单的物流、关税与偏远地区附加费单独说明。",
        invoice_enabled: true,
        shipping_template_note: "运费模板由工作室配置。",
      };
    return {
      returns_reference: resolveText(raw.returns_reference, preferences.locale),
      cross_border_note: resolveText(raw.cross_border_note, preferences.locale),
      invoice_enabled: raw.invoice_enabled !== false,
      shipping_template_note: resolveText(raw.shipping_template_note, preferences.locale),
      regions: raw.regions ?? {},
    };
  });

  /**
   * 站点浏览区（C02/C24）：**语言决定浏览区**（简中 → 中国大陆区、英文 → 海外/美国区）。
   *
   * 浏览区只决定"看哪种币种的定价"，结算区仍以**收货地址**为准（服务端裁定）；
   * 两者不一致时结算会被服务端拒绝并说明，而不是按汇率折算。
   */
  const browsingZone = computed(() => (preferences.locale === "en-US" ? "US" : "CN"));

  /** 浏览区对应的结算币种（未配置该区域时回落到站点币种 CNY）。 */
  const browsingCurrency = computed<"CNY" | "USD">(() => {
    const currency = commercePolicy.value.regions?.[browsingZone.value]?.currency;
    return currency === "USD" ? "USD" : "CNY";
  });
  const featureFlags = computed<Record<string, boolean>>(
    () => settings.value["platform.feature_flags"] ?? {},
  );

  function featureEnabled(name: string): boolean {
    return featureFlags.value[name] === true;
  }

  async function load(force = false): Promise<void> {
    if (loaded.value && !force) return;
    loading.value = true;
    try {
      settings.value = await fetchPublicSettings();
      error.value = null;
    } catch (caught) {
      error.value = caught instanceof Error ? caught.message : "站点配置加载失败";
      // 配置加载失败不阻塞浏览：使用文档基线值继续渲染，并在页脚提示。
    } finally {
      loaded.value = true;
      loading.value = false;
    }
  }

  function setScheduleSummary(summary: ScheduleSummary | null): void {
    scheduleSummary.value = summary;
    commissionOpen.value = summary ? summary.status === "open" : null;
  }

  return {
    settings,
    loaded,
    loading,
    error,
    scheduleSummary,
    commissionOpen,
    brand,
    socialLinks,
    commissionPolicy,
    loyaltyRules,
    commercePolicy,
    browsingZone,
    browsingCurrency,
    featureFlags,
    featureEnabled,
    load,
    setScheduleSummary,
  };
});
