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
  login: {
    eyebrow: "客户账号登录",
    title: "手机号验证码登录",
    intro: "浏览不需要登录；提交询价、查看订单与进度时才需要。首次验证通过会按需创建账号。",
    securityTitle: "工作人员不会向你索取验证码。",
    securityBody:
      "任何人以「客服」「补单」「退款」为由索要验证码都是冒充。验证码只在 5 分钟内有效、只能使用一次；我们不会把它写进日志或回复消息。",
    helpTitle: "登录遇到问题？",
    helpRights: "注销账号、访问或更正资料、撤回展示授权等数据权利请求，不必先登录：",
    helpComplaintLead: "投诉与售后同样保留公开入口：",
    helpContact: "联系与反馈",
    helpOr: "，或查看",
    helpPrivacy: "隐私政策与第三方清单",
    helpStaff: "员工使用独立后台入口与多因素验证，客户登录不会获得员工权限。",
    formTitle: "使用短信验证码登录",
    formHint: "未勾选协议不能提交，服务端也会再次拒绝；我们不会代替你勾选。",
    dialLabel: "区号",
    dialCn: "中国大陆 +86",
    dialUs: "美国 / 加拿大 +1",
    phoneLabel: "手机号",
    phoneHint: "用于接收验证码与订单通知；不会出现在公开页面。",
    phonePlaceholder: "11 位手机号",
    otpHint: "点击「获取验证码」后，短信里的 6 位数字会出现在这里；支持整段粘贴。",
    agreementLead: "我已阅读并同意",
    terms: "《用户服务协议》",
    agreementAnd: "与",
    privacy: "《隐私政策》",
    agreementTail: "，知悉短信验证码只用于本次登录与敏感操作验证。",
    submit: "登录 / 注册",
    goToTarget: "先去目标页面（登录后可返回）",
    returnLead: "登录后回到",
    returnNote:
      "。返回目标只允许本站相对路径，`//evil.example` 这类外部地址与编码绕过写法一律忽略，不合法时回到「我的账户」。",
    devHintLabel: "开发提示",
    devHintNote:
      "该提示只在开发/测试环境返回，用于本地联调；生产环境不存在万能码、测试号码白名单，也不会把验证码写入响应。",
  },
  shop: {
    crumb: "商城",
    eyebrow: "Shop",
    title: "商城",
    description:
      "现货与配件可直接下单；掉落与服务按「申请 → 人工审批 → 购买」执行。售罄商品仍可浏览，是否可售以服务端在下单时的校验为准。",
    metaDescription:
      "现货、配件、周边与服务：按分类筛选并查看库存状态。售罄商品仍可浏览；可售性、价格与运费以下单时服务端校验为准。",
    characters: "角色领养",
    applications: "掉落与服务申请",
    filterLabel: "商品筛选",
    category: "分类",
    all: "全部",
    kindPrefix: "类型",
    cancel: "取消",
    searchLabel: "搜索商品",
    searchPlaceholder: "商品名称或关键词",
    search: "搜索",
    clearFilters: "清除筛选",
    urlNote: "分类与搜索词会写进地址栏，刷新后仍能看到同一批商品。",
    zoneNote: "本区结算币种：{currency}。币种由区域定价决定，本站不按汇率折算。",
    zoneMismatch: "其中 {count} 件商品没有 {currency} 定价，无法在本区结算（不按汇率折算）。",
    notPricedInZone: "该商品没有 {currency} 定价，本区暂不能下单；可联系客服确认其他方式。",
    currentCategory: "当前分类",
    loading: "正在加载商品列表",
    forbiddenTitle: "没有访问商城的权限",
    forbiddenBody: "商城商品对访客开放；如果这里被拒绝，可能是账号状态受限。登录后可确认账号状态。",
    login: "手机号登录",
    contact: "联系客服",
    errorTitle: "商品列表暂时无法加载",
    errorNote: "筛选条件仍保留在地址栏，可直接重试。",
    reload: "重新加载",
    matchedCount: "符合条件的商品 {count} 件",
    totalCount: "共 {count} 件商品",
    page: "第 {page} 页",
    previous: "上一页",
    firstPage: "已是第一页",
    next: "下一页",
    allShown: "已显示全部结果",
    emptyFilteredTitle: "没有符合条件的商品",
    emptyFilteredBody:
      "当前分类或搜索词下没有已上架商品。售罄商品仍会出现在列表里，因此这里确实是没有匹配结果。",
    emptyTitle: "商城商品正在上架",
    emptyBody: "上架前需要完成图片授权、SKU 与库存核对；这里不会用占位商品填充页面。",
    lookCharacters: "先看看角色领养",
    askStock: "询问现货情况",
  },
  catalog: {
    categoryStock: "现货",
    categoryAccessory: "配件",
    categoryMerch: "周边",
    categoryService: "服务",
    kindStockGoods: "现货商品",
    kindExclusiveCharacter: "独占角色",
    kindCommission: "定制单",
    kindService: "服务",
  },
  identity: {
    phoneEmpty: "请先填写手机号（中国大陆或美国号码）。",
    phoneRequiredShort: "请填写手机号。",
    phoneDigitsOnly: "手机号只能包含数字。",
    phoneFormatDetail:
      "手机号格式不正确：中国大陆号码为 1 开头的 11 位数字，美国号码为 10 位数字（可带 +1）。",
    phoneInvalid: "请填写正确的手机号：中国大陆 11 位或美国 10 位。",
    challengeMissing: "请先获取短信验证码。",
    codeInvalid: "请输入 6 位数字验证码。",
    codeInvalidDetail: "验证码为 6 位数字，请检查是否漏输或包含空格。",
    agreementRequired: "请先勾选同意《用户服务协议》与《隐私政策》。",
    agreementRequiredSubmit: "需要先勾选同意《用户服务协议》与《隐私政策》才能登录。",
    challengeUsed: "验证码已失效，请重新获取。",
    phoneRequiredToSend: "请先填写手机号，用于接收登录验证码。",
    network: "网络异常，请稍后重试。",
    smsRateLimited: "发送过于频繁，请稍后再试。",
    smsCodeInvalid: "验证码不正确，请重新输入。",
    sessionExpired: "登录状态已过期，请重新登录。",
    forbidden: "当前账号无权执行该操作。",
    unknown: "操作失败，请稍后重试。",
    // 服务端稳定错误码（06 文档）逐码文案；与 zh 原文一致，英文见 en-US。
    authRequired: "登录状态已失效，请重新登录后继续。",
    sessionExpiredDetail: "登录状态已过期，请重新登录后继续。",
    csrfInvalid: "页面停留时间过长，请刷新页面后重试。",
    smsRateLimitedDetail: "发送过于频繁，请等待倒计时结束后再试。",
    smsCodeInvalidDetail: "验证码不正确，请核对后重新输入。",
    smsCodeExpired: "验证码已过期，请重新获取。",
    smsChallengeUsed: "该验证码已被使用，请重新获取。",
    reauthRequired: "该操作需要重新验证身份，请先完成一次短信验证码验证。",
    sensitiveVerificationRequired: "该操作需要先完成一次短信验证码验证。",
    validationError: "提交内容未通过校验，请按字段提示修改。",
    versionConflict: "资料已被其他操作更新，请刷新后重新提交。",
    stateConflict: "当前状态不允许该操作，请刷新后重试。",
    idempotencyConflict: "请求重复，已返回第一次的处理结果。",
    resourceNotFound: "没有找到符合条件的记录。",
    rateLimited: "操作过于频繁，请稍后再试。",
    providerUnavailable: "服务暂时不可用，请稍后重试。",
    internalError: "服务出现异常，请稍后重试。",
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
