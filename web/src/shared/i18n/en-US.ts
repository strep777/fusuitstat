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
  login: {
    eyebrow: "Customer account",
    title: "Log in with your phone number",
    intro:
      "Browsing needs no account; you only log in to send an inquiry or to view orders and progress. An account is created on first successful verification.",
    securityTitle: "Our staff will never ask you for a verification code.",
    securityBody:
      "Anyone asking for a code while claiming to be support, to fix an order or to process a refund is impersonating us. Codes are valid for five minutes and can be used once; we never write them to logs or replies.",
    helpTitle: "Trouble signing in?",
    helpRights:
      "You do not need to log in to request account closure, access or correction of your data, or withdrawal of a display authorisation:",
    helpComplaintLead: "Complaints and after-sales also keep a public channel:",
    helpContact: "Contact & feedback",
    helpOr: ", or read the",
    helpPrivacy: "privacy policy and third-party list",
    helpStaff:
      "Staff use a separate back-office entry point with multi-factor authentication; a customer login never grants staff permissions.",
    formTitle: "Log in with an SMS code",
    formHint:
      "The form cannot be submitted without accepting the agreements, and the server rejects it again; we never tick it for you.",
    dialLabel: "Country code",
    dialCn: "Mainland China +86",
    dialUs: "United States / Canada +1",
    phoneLabel: "Phone number",
    phoneHint: "Used for verification codes and order notifications; never shown on public pages.",
    phonePlaceholder: "11-digit phone number",
    otpHint:
      "After you tap “Send code”, the 6 digits from the SMS go here; pasting the whole message works too.",
    agreementLead: "I have read and accept the",
    terms: "Terms of Service",
    agreementAnd: "and the",
    privacy: "Privacy Policy",
    agreementTail:
      ", and understand that the SMS code is used only for this login and for sensitive operations.",
    submit: "Log in / sign up",
    goToTarget: "Go to the target page first (you can return after logging in)",
    returnLead: "After logging in you will return to",
    returnNote:
      ". Only same-site relative paths are allowed; external addresses such as `//evil.example` and encoding tricks are ignored, and an invalid target falls back to “My account”.",
    devHintLabel: "Development hint",
    devHintNote:
      "This hint is returned only in development and test environments for local integration; production has no universal code, no test-number allowlist, and never writes the code into the response.",
  },
  identity: {
    phoneEmpty: "Enter your phone number first (mainland China or US).",
    phoneRequiredShort: "Enter your phone number.",
    phoneDigitsOnly: "A phone number may contain digits only.",
    phoneFormatDetail:
      "That phone number looks wrong: mainland China numbers have 11 digits starting with 1, US numbers have 10 digits (a leading +1 is fine).",
    phoneInvalid: "Enter a valid phone number: 11 digits for mainland China or 10 for the US.",
    challengeMissing: "Request an SMS code first.",
    codeInvalid: "Enter the 6-digit verification code.",
    codeInvalidDetail:
      "The code is 6 digits; check for missing characters or stray spaces.",
    agreementRequired: "Please accept the Terms of Service and the Privacy Policy first.",
    agreementRequiredSubmit:
      "You need to accept the Terms of Service and the Privacy Policy before logging in.",
    challengeUsed: "The code is no longer valid; request a new one.",
    phoneRequiredToSend: "Enter your phone number first so we can send a login code.",
    network: "Network problem; please try again shortly.",
    smsRateLimited: "Too many requests; please try again later.",
    smsCodeInvalid: "That code is not correct; please enter it again.",
    smsCodeExpired: "The code has expired; request a new one.",
    sessionExpired: "Your session has expired; please log in again.",
    forbidden: "This account is not allowed to perform that action.",
    unknown: "The action failed; please try again shortly.",
    authRequired: "Your session has ended; please log in again to continue.",
    sessionExpiredDetail: "Your session has expired; please log in again to continue.",
    csrfInvalid: "The page was open too long; please refresh and try again.",
    smsRateLimitedDetail: "Sending too often; wait for the countdown to finish and try again.",
    smsCodeInvalidDetail: "That code is not correct; please check it and enter it again.",
    smsChallengeUsed: "That code has already been used; request a new one.",
    reauthRequired:
      "This action needs you to verify your identity again; complete one SMS code verification first.",
    sensitiveVerificationRequired:
      "This action needs one SMS code verification before it can continue.",
    validationError: "The submission did not pass validation; adjust the fields marked below.",
    versionConflict: "This record was updated elsewhere; refresh and submit again.",
    stateConflict: "The current state does not allow this action; refresh and try again.",
    idempotencyConflict: "Duplicate request; the first result has been returned.",
    resourceNotFound: "No matching record was found.",
    rateLimited: "Too many requests; please try again later.",
    providerUnavailable: "The service is temporarily unavailable; please try again later.",
    internalError: "Something went wrong on our side; please try again later.",
  },
};
