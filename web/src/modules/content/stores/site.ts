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
  type SocialLink,
} from "@/modules/content/api/content";

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
  const settings = ref<PublicSettings>({});
  const loaded = ref(false);
  const loading = ref(false);
  const error = ref<string | null>(null);
  const commissionOpen = ref<boolean | null>(null);
  const scheduleSummary = ref<ScheduleSummary | null>(null);

  const brand = computed<BrandProfile>(() => settings.value["brand.profile"] ?? FALLBACK_PROFILE);
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
  const commercePolicy = computed<CommercePolicy>(
    () =>
      settings.value["commerce.policy"] ?? {
        returns_reference: "参照淘宝 / 闲鱼平台规则执行",
        cross_border_note: "跨境订单的物流、关税与偏远地区附加费单独说明。",
        invoice_enabled: true,
        shipping_template_note: "运费模板由工作室配置。",
      },
  );
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
    featureFlags,
    featureEnabled,
    load,
    setScheduleSummary,
  };
});
