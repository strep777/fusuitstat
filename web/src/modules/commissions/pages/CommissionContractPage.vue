<script setup lang="ts">
/**
 * 合同说明与示例。
 *
 * 规则源：文档/07（合同与条款）。
 * - 全页标注“示例”：真实条款只在订单内、按接受时的版本留证。
 * - 默认用“预付款”，不把所有首期付款都称“定金”。
 * - 不复制参考站的免责、逾期没收或最终解释权条款。
 * - 明确说明：网站勾选留证不等同具有可靠电子签名能力。
 * 金额与期限数值全部读取服务端配置，示例金额只是计算演示。
 */
import { computed, onMounted } from "vue";

import { useSiteStore } from "@/modules/content/stores/site";
import { useDocumentTitle } from "@/modules/commissions/api/commissions";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatMoneyExact } from "@/shared/lib/format";

const site = useSiteStore();
const setTitle = useDocumentTitle();

onMounted(() => {
  setTitle("合同说明与示例");
  void site.load();
});

/** 示例总额：只用于演示分项计算，不代表任何真实报价。 */
const EXAMPLE_TOTAL_CENTS = 1_280_000;

const prepaymentCents = computed(() =>
  Math.round((EXAMPLE_TOTAL_CENTS * site.commissionPolicy.prepayment_percent) / 100),
);
const balanceCents = computed(() => EXAMPLE_TOTAL_CENTS - prepaymentCents.value);

const clauseGroups = computed(() => [
  {
    key: "parties",
    title: "一、经营主体与合同双方",
    items: [
      `经营主体：${site.brand.legal_entity}（官网 ${site.brand.domain}）。`,
      "客户：以本站账号实名手机号为准的下单本人。",
      `客服渠道：${site.brand.support_email}，工作时段 ${site.brand.service_hours}。`,
      "本页展示的条款为示例结构；真实条款以订单接受时记录的版本号与正文摘要为准。",
    ],
  },
  {
    key: "scope",
    title: "二、委托范围",
    items: [
      "范围由询价记录、工作室发布的报价明细和双方确认的变更单共同确定。",
      "报价中的“包含项”与“不含项”分别列出，未写入包含项的内容默认不含。",
      "客户需要提供有权使用的角色设定；因设定权属引起的争议由客户负责说明来源。",
    ],
  },
  {
    key: "amount",
    title: "三、金额与币种",
    items: [
      "金额以报价与订单中记录的服务端版本为准，聊天记录里的口头价格不构成可支付金额。",
      `示例：报价总额 ${formatMoneyExact(EXAMPLE_TOTAL_CENTS)}（此数字仅为计算演示，不是真实报价）。`,
      "若报价过期或已发布新版本，旧版本不能转为订单，需要查看最新版本后重新确认。",
    ],
  },
  {
    key: "prepayment",
    title: "四、预付款与尾款",
    items: [
      `首期款项按报价总额的 ${site.commissionPolicy.prepayment_percent}% 计算，本页统一称为“预付款”，不称为“定金”。`,
      `示例首期：${formatMoneyExact(prepaymentCents.value)}；示例尾款：${formatMoneyExact(balanceCents.value)}。`,
      `尾款在交付前 ${site.commissionPolicy.balance_due_days} 天内结清；未完成尾款校验不安排发货。`,
      "采用具有定金法律效果的安排属于待确认事项，未确认前不会在页面上把预付款称作定金。",
    ],
  },
  {
    key: "delivery",
    title: "五、交付安排与修改次数",
    items: [
      "预计开始与预计交付时间写在报价里，属于计划值；实际以制作页面每个阶段的记录为准。",
      `每单包含 ${site.commissionPolicy.free_revision_count} 次免费修改，范围是阶段确认时提出的合理调整。`,
      "若因等待客户回复造成延期，制作页面会留下可查记录，不把等待时间算作工作室延期。",
      "出现工作室原因的延期时，会更新预计时间并说明原因，不隐瞒进度。",
    ],
  },
  {
    key: "change",
    title: "六、变更与加项",
    items: [
      "重大加项先生成变更单，写清新增金额、延期天数与条款版本，客户接受后才执行。",
      "加项产生的款项单独生成补款，已经成功的付款记录与旧条款快照不会被改写。",
      "减项通过退款或减少未付款项表达，账务保持可追踪。",
    ],
  },
  {
    key: "cancel",
    title: "七、取消与退款",
    items: [
      "取消请求按订单所处阶段处理：未开工、已备料、已开工对应的已发生成本不同，扣除范围会在处理时逐项说明。",
      "退款走原支付渠道，不用星绒币替代现金退款，也不要求客户为负账面余额补现金。",
      "取消后角色是否重新上架由运营审核，不因退款回调自动公开你的设定图或复售。",
      "本页不设“逾期视为放弃”“款项一律不退”“最终解释权归工作室”一类条款。",
    ],
  },
  {
    key: "quality",
    title: "八、质量与售后",
    items: [
      "交付前按阶段核对结构、外观与佩戴安全性；出厂时提供本单的出厂记录。",
      "属于制作质量问题的脱胶、开线、结构松动，在约定保修期内由工作室处理，运费按责任方承担。",
      "售后义务不因客户是否同意公开成品图、是否给出好评而改变。",
    ],
  },
  {
    key: "image-rights",
    title: "九、图像公开授权",
    items: [
      "成品图的公开展示授权单独选择，默认不授权。",
      "拒绝授权不影响下单、不影响价格，也不影响售后。",
      "授权后仍可撤回；撤回后已印制的历史物料不作追溯，但不再新增公开使用。",
    ],
  },
]);

const evidenceItems = computed(() => [
  `接受报价与付款前，页面会展示条款版本号（当前配置：${site.commissionPolicy.terms_version}）、金额、范围、修改次数、取消规则与售后安排。`,
  "你主动勾选同意后，系统记录条款版本、正文摘要哈希、订单号、同意时间与必要的审计信息，用于事后复核。",
  "网站勾选留证不等同具有可靠电子签名能力：它能证明你在该时间接受了该版本条款，但不等于完成了符合电子签名法要求的可靠电子签名。",
  "是否需要引入第三方电子签服务属于待确认事项；在确认前，重大合同以线下签署或双方另行确认的书面文件为准。",
]);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '委托', to: '/commission/guide' },
        { label: '合同说明与示例' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <div class="flex flex-wrap items-center gap-3">
        <p class="eyebrow">Contract</p>
        <StatusBadge tone="amber" label="示例" />
      </div>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">合同说明与示例</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        本页用于说明合同里会出现哪些内容、每一条在保护什么。它是结构与措辞示例，
        <strong class="font-medium text-ink-soft">不是可签署的正式合同</strong>；真实接受记录只发生在订单内，
        并按你接受时的条款版本留证。
      </p>
    </header>

    <div class="mt-8 rounded-lg border border-amber bg-amber-soft p-5 text-sm text-amber-strong">
      <p class="font-medium">请先读这一条</p>
      <ul class="mt-3 space-y-2">
        <li>本页所有条款文本都是示例，不构成对任何具体委托的承诺或约束。</li>
        <li>本页出现的金额都是计算演示，不是报价；报价只由工作人员在后台发布，并带有效期。</li>
        <li>没有订单号的页面截图不能作为条款同意的证据；同意记录保存在订单内。</li>
      </ul>
    </div>

    <!-- 合同条款里的比例、期限与版本号都来自服务端配置：配置没读到之前不展示示例数值，
         避免把文档基线值当成「当前配置」呈现给用户（03 文档：数据页必须有 loading/error）。 -->
    <section v-if="site.loading && !site.loaded" class="mt-12" aria-live="polite">
      <SkeletonBlock :lines="3" aria-label="正在读取合同配置" />
    </section>
    <EmptyState
      v-else-if="site.error"
      class="mt-12"
      tone="error"
      title="合同配置暂时无法加载"
      :description="`${site.error} 条款示例里的比例、期限与版本号都取自服务端配置，读到之前不展示具体数值，也不使用文档基线值冒充当前配置。`"
    >
      <AppButton size="sm" @click="site.load(true)">重新加载配置</AppButton>
      <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
    </EmptyState>

    <div v-else class="mt-12 grid gap-6 lg:grid-cols-[1.15fr_0.85fr]">
      <div class="space-y-6">
        <SurfaceCard v-for="group in clauseGroups" :key="group.key">
          <div class="flex items-start justify-between gap-4">
            <h2 class="font-display text-lg">{{ group.title }}</h2>
            <StatusBadge tone="neutral" label="示例" />
          </div>
          <ul class="mt-4 space-y-3 text-sm leading-relaxed text-ink-muted">
            <li v-for="line in group.items" :key="line" class="flex gap-3">
              <span class="mt-2 size-1.5 shrink-0 rounded-full bg-line-strong" aria-hidden="true" />
              <span>{{ line }}</span>
            </li>
          </ul>
        </SurfaceCard>
      </div>

      <div class="space-y-6 lg:sticky lg:top-24 lg:self-start">
        <SurfaceCard>
          <h2 class="font-display text-lg">同意记录与电子签名</h2>
          <ul class="mt-4 space-y-3 text-sm leading-relaxed text-ink-muted">
            <li v-for="line in evidenceItems" :key="line" class="flex gap-3">
              <span class="mt-2 size-1.5 shrink-0 rounded-full bg-amber" aria-hidden="true" />
              <span>{{ line }}</span>
            </li>
          </ul>
        </SurfaceCard>

        <SurfaceCard>
          <h2 class="font-display text-lg">当前配置数值</h2>
          <dl class="mt-4 space-y-2 text-sm">
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">条款版本</dt>
              <dd class="text-ink-soft">{{ site.commissionPolicy.terms_version }}</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">报价有效期</dt>
              <dd class="tabular-nums text-ink-soft">{{ site.commissionPolicy.quote_valid_days }} 天</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">预付款比例</dt>
              <dd class="tabular-nums text-ink-soft">{{ site.commissionPolicy.prepayment_percent }}%</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">尾款期限</dt>
              <dd class="tabular-nums text-ink-soft">{{ site.commissionPolicy.balance_due_days }} 天</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">免费修改次数</dt>
              <dd class="tabular-nums text-ink-soft">{{ site.commissionPolicy.free_revision_count }} 次</dd>
            </div>
            <div class="flex justify-between gap-4">
              <dt class="text-ink-faint">预留时长</dt>
              <dd class="tabular-nums text-ink-soft">{{ site.commissionPolicy.slot_hold_minutes }} 分钟</dd>
            </div>
          </dl>
          <p class="mt-4 text-xs text-ink-faint">
            这些数值来自服务端配置，页面展示与实际校验使用同一份配置；配置更新后本页会随之变化。
          </p>
        </SurfaceCard>

        <SurfaceCard>
          <h2 class="font-display text-lg">本页不包含</h2>
          <ul class="mt-4 space-y-3 text-sm text-ink-muted">
            <li>不设“逾期未付款即没收已付款项”的条款。</li>
            <li>不设“工作室对一切争议享有最终解释权”的条款。</li>
            <li>不设免除工作室自身质量责任的概括免责条款。</li>
            <li>不把客户同意公开成品图作为享受售后的前提。</li>
          </ul>
        </SurfaceCard>
      </div>
    </div>

    <section class="mt-14">
      <SectionHeading
        eyebrow="Next"
        title="接下来做什么"
        description="先看档期确认是否开放，再提交询价；收到报价后，在接受前把条款版本和金额核对一遍。"
      >
        <template #actions>
          <AppButton to="/commission/schedules" variant="secondary" size="sm">查看档期与名额</AppButton>
          <AppButton to="/estimate" size="sm">开始估价</AppButton>
        </template>
      </SectionHeading>
    </section>
  </div>
</template>
