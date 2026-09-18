/**
 * 站点文案词条（简体中文，权威形状）。
 *
 * 约束（C02/C24、02 文档 FR-022）：
 * - 首发只有简中（zh-CN）与英文（美）（en-US）两种语言，**不输出未上线语言的假入口**；
 * - 这里是词条的**唯一形状来源**：`en-US` 必须与它键集完全一致（`scripts/check_i18n.py` 门禁）；
 * - 键名按界面区域分组，值只放文案，不放业务数值——金额、比例、时效一律来自服务端配置，
 *   翻译层不得成为第二份业务规则源。
 */

export const zhCN = {
  common: {
    brandShort: "弈星纪元",
    brandFull: "弈星纪元兽装工作室",
    brandEnglish: "STARIUM FURSUIT STUDIO",
    brandTagline: "绒里有星，装配成纪",
    skipToContent: "跳到主要内容",
    loading: "正在加载",
    retry: "重试",
    close: "关闭",
    back: "返回",
    language: "语言",
    languageZh: "简体中文",
    languageEn: "English (US)",
    languageUnavailable: "英文站尚未上线",
  },
  nav: {
    mainLabel: "主导航",
    mobileLabel: "移动导航",
    home: "首页",
    works: "作品",
    series: "系列",
    commission: "委托",
    characters: "角色",
    shop: "商城",
    news: "公告",
    about: "关于",
    account: "我的账户",
    login: "登录",
    loginMobile: "手机号登录",
    contact: "联系与反馈",
    openMenu: "打开导航菜单",
    closeMenu: "关闭导航菜单",
    brandHome: "弈星纪元兽装工作室首页",
    scheduleOpen: "委托开放中",
    scheduleClosed: "委托暂未开放",
    scheduleUnknown: "查看档期",
    unread: "，{count} 条未读通知",
    themeToLight: "切换到浅色主题",
    themeToDark: "切换到深色主题",
  },
  footer: {
    browse: "浏览",
    help: "委托与帮助",
    legal: "规则与联系",
    social: "社交渠道",
    legalEntity: "经营主体",
    supportEmail: "客服邮箱",
    serviceHours: "工作时段",
    responseSla: "人工响应",
    responseSlaValue: "询价 {inquiry} 个工作日、反馈 {feedback} 个工作日首响",
    intro: "手工制作的全装、半装、单头与配件定制工作室。委托先询价、再报价、签合同后进入排期，全程可在账户内查看进度。",
    works: "作品集",
    series: "系列规格",
    characters: "角色领养",
    shop: "商城",
    news: "公告",
    guide: "委托指南",
    schedules: "档期与名额",
    contract: "合同示例",
    faq: "常见问题",
    care: "养护与清洁",
    terms: "用户服务协议",
    privacy: "隐私政策",
    returns: "取消与退换规则",
    points: "星绒币规则",
    contact: "联系与反馈",
    feedback: "提交问题或建议",
    reduceMotion: "减弱动效",
    themeToLight: "切换浅色主题",
    themeToDark: "切换深色主题",
    configFallback: "站点配置未能从服务端加载，当前显示的是文档基线值。部分运营信息可能不是最新。",
  },
} as const;

/** 词条树：值是文案，或下一层子树。 */
export type MessageTree = { readonly [key: string]: string | MessageTree };

/** 与中文词条同形状、但值为任意文案的类型（英文词条用它保证键集一致）。 */
export type MessageShape<T> = {
  readonly [K in keyof T]: T[K] extends string ? string : MessageShape<T[K]>;
};

/** 由中文词条推导的点号路径键（`t()` 只接受真实存在的键，写错即编译失败）。 */
export type DotKeys<T, Prefix extends string = ""> = {
  [K in keyof T & string]: T[K] extends string
    ? Prefix extends ""
      ? K
      : `${Prefix}.${K}`
    : DotKeys<T[K], Prefix extends "" ? K : `${Prefix}.${K}`>;
}[keyof T & string];

export type MessageKey = DotKeys<typeof zhCN>;

/** 中文词条的形状（英文词条用它声明「键集必须一致」）。 */
export type MessageShapeOfDefault = MessageShape<typeof zhCN>;
