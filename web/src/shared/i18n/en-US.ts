/**
 * American English messages (en-US).
 *
 * 约束（C02/C24）：
 * - 键集必须与 `zh-CN` 完全一致（`scripts/check_i18n.py` 门禁，键缺失或多出都失败）；
 * - 品牌名与经营主体的**英文写法沿用代码中既有的事实**（`STARIUM FURSUIT STUDIO`、
 *   `starium.cn`），不新造公司英文名；法定主体名保留中文原样，避免出现未核准的英文登记名；
 * - 业务数值（金额、比例、工作日）不写进词条，一律来自服务端配置。
 */

import type { MessageShapeOfDefault } from "@/shared/i18n/zh-CN";

export const enUS: MessageShapeOfDefault = {
  common: {
    brandShort: "Starium",
    brandFull: "Starium Fursuit Studio",
    brandEnglish: "STARIUM FURSUIT STUDIO",
    brandTagline: "Stars in the fur, eras in the making",
    skipToContent: "Skip to main content",
    loading: "Loading",
    retry: "Retry",
    close: "Close",
    back: "Back",
    language: "Language",
    languageZh: "简体中文",
    languageEn: "English (US)",
    languageUnavailable: "The English site is not live yet",
  },
  nav: {
    mainLabel: "Main navigation",
    mobileLabel: "Mobile navigation",
    home: "Home",
    works: "Works",
    series: "Series",
    commission: "Commissions",
    characters: "Characters",
    shop: "Shop",
    news: "News",
    about: "About",
    account: "My account",
    login: "Log in",
    loginMobile: "Log in with phone",
    contact: "Contact & feedback",
    openMenu: "Open navigation menu",
    closeMenu: "Close navigation menu",
    brandHome: "Starium Fursuit Studio home",
    scheduleOpen: "Commissions open",
    scheduleClosed: "Commissions closed",
    scheduleUnknown: "See schedule",
    unread: ", {count} unread notifications",
    themeToLight: "Switch to light theme",
    themeToDark: "Switch to dark theme",
  },
  footer: {
    browse: "Browse",
    help: "Commissions & help",
    legal: "Rules & contact",
    social: "Social channels",
    legalEntity: "Legal entity",
    supportEmail: "Support email",
    serviceHours: "Service hours",
    responseSla: "Human response",
    responseSlaValue: "First reply within {inquiry} business days for inquiries and {feedback} for feedback",
    intro:
      "A studio crafting full suits, partials, heads and accessories by hand. Commissions start with an inquiry, then a quote, then a signed contract before scheduling; progress is visible in your account.",
    works: "Works",
    series: "Series specs",
    characters: "Character adoption",
    shop: "Shop",
    news: "News",
    guide: "Commission guide",
    schedules: "Schedule & slots",
    contract: "Sample contract",
    faq: "FAQ",
    care: "Care & cleaning",
    terms: "Terms of service",
    privacy: "Privacy policy",
    returns: "Cancellation & returns",
    points: "Reward points rules",
    contact: "Contact & feedback",
    feedback: "Send a question or suggestion",
    reduceMotion: "Reduce motion",
    themeToLight: "Switch to light theme",
    themeToDark: "Switch to dark theme",
    configFallback:
      "Site settings could not be loaded from the server, so documented baseline values are shown. Some operational details may be out of date.",
  },
};
