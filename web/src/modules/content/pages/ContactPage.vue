<script setup lang="ts">
/**
 * 联系与反馈（FR-001、FR-018）。
 *
 * 信息顺序：工作时段与响应时间 → 联系渠道 → 隐私与投诉的公开路径 → 社交渠道 → 自助入口。
 * 约束：社交链接只在 URL 为 https 时输出；行使隐私权利与投诉不强制绑定短信登录——
 * 客服邮箱是可以不登录直接使用的公开路径，反馈表单只是登录用户的额外选择。
 */
import { computed, onMounted } from "vue";
import { RouterLink } from "vue-router";

import { applyPageHead } from "@/modules/content/api/content";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SurfaceCard } from "@/shared/ui/base";

const site = useSiteStore();

/** 外链只允许安全协议（03 文档：外链只允许后台配置的安全协议）。 */
const safeSocial = computed(() => site.socialLinks.filter((link) => /^https:\/\//i.test(link.url.trim())));

const breadcrumbs = [
  { label: "首页", to: "/" },
  { label: "联系与反馈" },
];

const channels = computed(() => [
  {
    title: "客服邮箱",
    description: "不登录也能使用。写清订单号或询价编号会更快定位问题。",
    action: { label: site.brand.support_email, href: `mailto:${site.brand.support_email}` },
  },
  {
    title: "在线反馈",
    description: "手机号登录后提交，可以附页面路径与截图，处理进度在账户内可查。",
    action: { label: "提交反馈", to: "/feedback/new" },
  },
  {
    title: "委托与报价",
    description: "先公开估价，无需登录；确认方向后再登录提交询价与设定图。",
    action: { label: "开始估价", to: "/estimate" },
  },
]);

const selfService = [
  { label: "委托指南：可接范围、步骤、量体与售后", to: "/commission/guide" },
  { label: "常见问题：报价、修改、交期与售后的常见疑问", to: "/faq" },
  { label: "养护与清洁：穿戴、清洁、运输与存放", to: "/care" },
  { label: "档期与名额：当前开放状态与截止时间", to: "/commission/schedules" },
  { label: "订单与售后：登录后查看订单、退款与开票", to: "/account/orders" },
];

onMounted(() => {
  applyPageHead({
    title: "联系与反馈",
    description:
      "工作室工作时段、客服邮箱与反馈入口；隐私请求与投诉可以不登录，直接通过公开客服邮箱提交。",
    canonical: "/contact",
    ogType: "website",
  });
  void site.load();
});
</script>

<template>
  <div>
    <div class="container-page pt-8">
      <Breadcrumbs :items="breadcrumbs" />
    </div>

    <section class="container-page pb-10 pt-6">
      <SectionHeading
        eyebrow="Contact"
        level="1"
        title="联系与反馈"
        description="人工处理时间以工作时段为准；节假日顺延会在公告里说明。所有渠道都由真人查看，没有自动承诺或机器报价。"
      />
    </section>

    <!-- 工作时段与响应时间 -->
    <section class="container-page pb-12">
      <SurfaceCard as="div">
        <dl class="grid gap-6 text-sm sm:grid-cols-3">
          <div>
            <dt class="eyebrow">工作时段</dt>
            <dd class="mt-2 text-ink-soft">{{ site.brand.service_hours }}</dd>
          </div>
          <div>
            <dt class="eyebrow">询价首响</dt>
            <dd class="mt-2 text-ink-soft">{{ site.brand.response_sla.inquiry_workdays }} 个工作日</dd>
          </div>
          <div>
            <dt class="eyebrow">反馈首响</dt>
            <dd class="mt-2 text-ink-soft">{{ site.brand.response_sla.feedback_workdays }} 个工作日</dd>
          </div>
        </dl>
        <p class="mt-6 text-xs text-ink-faint">
          首响指人工第一次回复的时间，不等于处理完成时间。数值来自后台配置，修改后会同步显示在这里。
        </p>
      </SurfaceCard>
    </section>

    <!-- 联系渠道 -->
    <section class="border-y border-line bg-surface-muted">
      <div class="container-page py-14">
        <SectionHeading eyebrow="Channels" title="联系渠道" />

        <div class="mt-8 grid gap-6 md:grid-cols-3">
          <SurfaceCard v-for="channel in channels" :key="channel.title" class="flex h-full flex-col">
            <h3 class="font-display text-lg">{{ channel.title }}</h3>
            <p class="mt-3 flex-1 text-sm leading-relaxed text-ink-muted">{{ channel.description }}</p>
            <div class="mt-5">
              <AppButton
                v-if="channel.action.to"
                :to="channel.action.to"
                size="sm"
                variant="secondary"
              >
                {{ channel.action.label }}
              </AppButton>
              <a
                v-else
                class="link-underline text-sm font-medium text-amber-strong hover:text-amber"
                :href="channel.action.href"
              >
                {{ channel.action.label }}
              </a>
            </div>
          </SurfaceCard>
        </div>
      </div>
    </section>

    <!-- 隐私与投诉：公开路径，不强制登录 -->
    <section class="container-page py-14">
      <SectionHeading
        eyebrow="Privacy & complaints"
        title="隐私请求与投诉"
        description="行使查阅、更正、删除等权利，或者提出投诉、举报内容问题时，不需要短信登录。"
      />

      <div class="mt-8 grid gap-6 lg:grid-cols-[1.1fr_1fr]">
        <SurfaceCard as="div">
          <p class="text-sm leading-relaxed text-ink-soft">
            发邮件到
            <a class="link-underline hover:text-amber-strong" :href="`mailto:${site.brand.support_email}`">
              {{ site.brand.support_email }}
            </a>
            即可提交隐私请求或投诉，主题里写清诉求类型，正文说明涉及的账号（手机号可只写后四位）、订单号与希望的处理方式。
            工作室会按隐私政策列明的期限回复；无法处理的请求会说明原因和可行的替代路径。
          </p>
          <ul class="mt-5 space-y-2 text-sm text-ink-muted">
            <li>· 需要核验身份时，只要求能证明账号归属的最小信息，不收集身份证照片等非必要材料。</li>
            <li>· 请勿在邮件中发送短信验证码、支付密码或银行卡完整卡号。</li>
            <li>· 已经无法登录账号时，同样可以通过这个邮箱提出请求。</li>
          </ul>
          <div class="mt-6 flex flex-wrap gap-3">
            <AppButton to="/legal/privacy" size="sm" variant="secondary">阅读隐私政策</AppButton>
            <AppButton to="/legal/returns" size="sm" variant="secondary">取消与退换规则</AppButton>
            <AppButton to="/legal/terms" size="sm" variant="secondary">用户服务协议</AppButton>
          </div>
        </SurfaceCard>

        <SurfaceCard as="div">
          <p class="eyebrow">经营主体</p>
          <dl class="mt-4 grid gap-4 text-sm">
            <div>
              <dt class="text-ink-faint">主体名称</dt>
              <dd class="mt-1">{{ site.brand.legal_entity }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">品牌</dt>
              <dd class="mt-1">{{ site.brand.brand_name }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">站点域名</dt>
              <dd class="mt-1">{{ site.brand.domain }}</dd>
            </div>
            <div>
              <dt class="text-ink-faint">投诉与权利请求</dt>
              <dd class="mt-1">
                <a class="link-underline hover:text-amber-strong" :href="`mailto:${site.brand.support_email}`">
                  {{ site.brand.support_email }}
                </a>
              </dd>
            </div>
          </dl>
          <p class="mt-6 text-xs text-ink-faint">
            只在本站与已配置的官方渠道沟通；对公账户与合同抬头以订单和合同文件为准。
          </p>
        </SurfaceCard>
      </div>
    </section>

    <!-- 社交渠道：只在 https 时输出 -->
    <section class="border-y border-line bg-surface-muted">
      <div class="container-page py-14">
        <SectionHeading
          eyebrow="Social"
          title="官方社交渠道"
          description="只有后台已配置且使用安全协议的地址才会显示；没有配置时这一区块保持为空，不输出占位入口。"
        />

        <ul v-if="safeSocial.length" class="mt-8 flex flex-wrap gap-3">
          <li v-for="link in safeSocial" :key="link.code">
            <a
              class="inline-flex min-h-[44px] items-center rounded-full bg-canvas-sunk px-5 text-sm transition-colors hover:bg-brand-soft hover:text-brand-strong"
              :href="link.url"
              target="_blank"
              rel="noopener noreferrer"
            >
              {{ link.label }}
            </a>
          </li>
        </ul>

        <EmptyState
          v-else
          class="mt-8"
          title="社交渠道尚未配置"
          description="渠道地址由运营在后台填写，且只允许 https 链接；配置完成前这里不会显示任何外部入口。"
        />
      </div>
    </section>

    <!-- 自助入口 -->
    <section class="container-page py-14">
      <SectionHeading eyebrow="Self service" title="先自助，再找人工" description="多数问题在这些页面里已经有明确答案。" />

      <ul class="mt-8 divide-y divide-line border-y border-line">
        <li v-for="item in selfService" :key="item.to">
          <RouterLink class="group flex items-center justify-between gap-4 py-4 text-sm" :to="item.to">
            <span class="text-ink-soft group-hover:text-amber-strong">{{ item.label }}</span>
            <span class="shrink-0 text-xs text-ink-faint" aria-hidden="true">进入</span>
          </RouterLink>
        </li>
      </ul>
    </section>
  </div>
</template>
