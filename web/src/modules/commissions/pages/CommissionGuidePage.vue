<script setup lang="ts">
/**
 * 委托指南。
 *
 * 信息顺序（03 文档页面职责）：可接范围 → 步骤 → 量体说明 → 售后。
 * 数值（报价有效期、预付款比例、尾款期限、免费修改次数、人工响应时间）
 * 全部来自服务端配置，页面不写死业务承诺。
 */
import { computed, onMounted } from "vue";

import { useSiteStore } from "@/modules/content/stores/site";
import { SUIT_TYPE_OPTIONS, useDocumentTitle } from "@/modules/commissions/api/commissions";
import { AppButton, Breadcrumbs, EmptyState, SectionHeading, SkeletonBlock, SurfaceCard } from "@/shared/ui/base";

const site = useSiteStore();
const setTitle = useDocumentTitle();

onMounted(() => {
  setTitle("委托指南");
  void site.load();
});

const steps = computed(() => [
  {
    step: "01",
    title: "提交询价",
    detail:
      "填写委托范围、设定图与制作需求。询价是需求沟通记录，不是订单，也不产生付款义务。",
    note: `人工首响为 ${site.brand.response_sla.inquiry_workdays} 个工作日；节假日另行说明。`,
  },
  {
    step: "02",
    title: "人工报价",
    detail:
      "工作人员按系列与结构评估后发布报价，明确明细、总额、首期与尾款、包含项与不含项。",
    note: `报价有效期 ${site.commissionPolicy.quote_valid_days} 天，过期后不能转到订单。`,
  },
  {
    step: "03",
    title: "接受报价与签约",
    detail:
      "在有效期内接受当前版本报价，同时确认条款版本；随后支付首期款项，名额转为正式占用。",
    note: `首期按总额的 ${site.commissionPolicy.prepayment_percent}% 计算，尾款在交付前 ${site.commissionPolicy.balance_due_days} 天内结清。`,
  },
  {
    step: "04",
    title: "制作与交付",
    detail:
      "进入制作队列后按阶段推进，每个阶段记录负责人、开始时间、预计日期与说明，需要你确认的阶段会单独提示。",
    note: `每单包含 ${site.commissionPolicy.free_revision_count} 次免费修改，超出部分与重大加项会先出变更单再执行。`,
  },
]);

const scopeIncludes = computed(() => [
  "兽装本体：全装、半装、单头与经确认的局部或配件组合。",
  "按设定图还原花纹、结构与可动需求；材料与工艺在报价中写明。",
  "结构安全评估：承重、散热、视野与佩戴安全性由工作室复核。",
]);

const scopeExcludes = computed(() => [
  "不承接要求复制他人已有作品、品牌形象或未授权角色的委托。",
  "不承接需要医疗、消防或工业防护等级的用途；兽装不是防护装备。",
  "不承接无法提供设定图或设定权属说明的委托。",
  "具体接单范围与当轮名额以档期页与公告为准，指南本身不构成接单承诺。",
]);
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '委托', to: '/commission/guide' },
        { label: '委托指南' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">Commission guide</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">委托指南</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        询价是需求沟通记录，报价是有期限的商业提案，订单才承载实际收款与履约。三者分开保存，
        每一步你都能看到当前状态和下一步动作。
      </p>
      <div class="mt-6 flex flex-wrap gap-3">
        <AppButton to="/estimate">开始估价</AppButton>
        <AppButton to="/commission/schedules" variant="secondary">查看档期与名额</AppButton>
      </div>
    </header>

    <!-- 可接范围 -->
    <section class="mt-14">
      <SectionHeading
        eyebrow="Scope"
        title="可接范围"
        description="范围写清楚，报价才有比较的基础。下面的包含项与不含项是评估口径，最终以你收到的那份报价为准。"
      />

      <div class="mt-8 grid gap-6 lg:grid-cols-2">
        <SurfaceCard>
          <h3 class="font-display text-lg">制作类型</h3>
          <dl class="mt-4 space-y-4 text-sm">
            <div v-for="item in SUIT_TYPE_OPTIONS" :key="item.value">
              <dt class="font-medium text-ink-soft">{{ item.label }}</dt>
              <dd class="mt-1 text-ink-muted">{{ item.hint }}</dd>
            </div>
          </dl>
          <p class="mt-5 rounded-sm bg-canvas-sunk px-4 py-3 text-xs text-ink-muted">
            系列参数与起价在「系列与规格」页对比，起价只代表配置基线，不承诺交付时间。
          </p>
          <div class="mt-4">
            <AppButton to="/fursuits" variant="secondary" size="sm">对比系列与规格</AppButton>
          </div>
        </SurfaceCard>

        <div class="grid gap-6">
          <SurfaceCard>
            <h3 class="font-display text-lg">包含</h3>
            <ul class="mt-4 space-y-3 text-sm text-ink-muted">
              <li v-for="line in scopeIncludes" :key="line" class="flex gap-3">
                <span class="mt-2 size-1.5 shrink-0 rounded-full bg-jade" aria-hidden="true" />
                <span>{{ line }}</span>
              </li>
            </ul>
          </SurfaceCard>

          <SurfaceCard>
            <h3 class="font-display text-lg">不含与不承接</h3>
            <ul class="mt-4 space-y-3 text-sm text-ink-muted">
              <li v-for="line in scopeExcludes" :key="line" class="flex gap-3">
                <span class="mt-2 size-1.5 shrink-0 rounded-full bg-line-strong" aria-hidden="true" />
                <span>{{ line }}</span>
              </li>
            </ul>
          </SurfaceCard>
        </div>
      </div>
    </section>

    <!-- 步骤 -->
    <section class="mt-16">
      <SectionHeading
        eyebrow="Steps"
        title="委托步骤"
        description="按顺序走完四步；每一步都可以暂停，草稿与历史版本都会保留。"
      />

      <ol class="mt-8 grid gap-6 sm:grid-cols-2">
        <li v-for="item in steps" :key="item.step">
          <SurfaceCard class="h-full">
            <p class="font-display text-3xl text-amber">{{ item.step }}</p>
            <h3 class="mt-3 font-display text-lg">{{ item.title }}</h3>
            <p class="mt-3 text-sm leading-relaxed text-ink-muted">{{ item.detail }}</p>
            <p class="mt-4 border-t border-line pt-3 text-xs text-ink-faint">{{ item.note }}</p>
          </SurfaceCard>
        </li>
      </ol>

      <div class="mt-8 rounded-lg border border-amber bg-amber-soft p-5 text-sm text-amber-strong">
        报价有效期 {{ site.commissionPolicy.quote_valid_days }} 天、首期
        {{ site.commissionPolicy.prepayment_percent }}%、尾款期限
        {{ site.commissionPolicy.balance_due_days }} 天、免费修改
        {{ site.commissionPolicy.free_revision_count }} 次，条款版本
        {{ site.commissionPolicy.terms_version }}。这些数值由后台配置，页面展示与服务端校验使用同一份配置。
      </div>
    </section>

    <!-- 量体说明 -->
    <section class="mt-16">
      <SectionHeading
        eyebrow="Measurements"
        title="量体说明"
        description="量体信息默认在接单后单独收集。首次询价只需要角色与制作需求，不收生日、性别和住址。"
      />

      <div class="mt-8 grid gap-6 lg:grid-cols-[1.1fr_0.9fr]">
        <SurfaceCard>
          <h3 class="font-display text-lg">什么时候量、量什么</h3>
          <ol class="mt-4 space-y-4 text-sm text-ink-muted">
            <li>
              <p class="font-medium text-ink-soft">1. 接受报价后</p>
              <p class="mt-1">
                订单建立后，制作页面会出现量体表。你可以在家自行测量并提交，测量方法以页面说明为准。
              </p>
            </li>
            <li>
              <p class="font-medium text-ink-soft">2. 单位与精度</p>
              <p class="mt-1">
                高度与围度使用厘米（cm），体重使用千克（kg）。表单显示单位并支持小数，
                例如头围 57.5 cm、体重 62.5 kg。
              </p>
            </li>
            <li>
              <p class="font-medium text-ink-soft">3. 异常范围只做提示</p>
              <p class="mt-1">
                超出常见范围时页面会请你复核一次，特殊体型由工作人员单独确认，
                不会因为数值超出范围就拒绝你的委托。
              </p>
            </li>
            <li>
              <p class="font-medium text-ink-soft">4. 数据用途</p>
              <p class="mt-1">
                量体数据只用于这次制作的版型与结构，按高敏感度内部资料保护，不公开、不用于其他订单。
              </p>
            </li>
          </ol>
        </SurfaceCard>

        <SurfaceCard>
          <h3 class="font-display text-lg">量体表包含的项目</h3>
          <ul class="mt-4 grid grid-cols-2 gap-x-4 gap-y-2 text-sm text-ink-muted">
            <li v-for="item in ['身高 (cm)', '头围 (cm)', '肩宽 (cm)', '胸围 (cm)', '腰围 (cm)', '臀围 (cm)', '臂长 (cm)', '腿长 (cm)', '脚长 (cm)', '体重 (kg)']" :key="item">
              {{ item }}
            </li>
          </ul>
          <p class="mt-5 text-xs text-ink-faint">
            具体项目随制作类型调整：单头只需要头围与头部尺寸，全装才会收集全身数据。
          </p>
        </SurfaceCard>
      </div>
    </section>

    <!-- 售后 -->
    <section class="mt-16">
      <SectionHeading
        eyebrow="After-sales"
        title="售后与修改"
        description="售后义务不因为你不愿公开宣传而被剥夺；是否授权公开成品图与你享受的售后无关。"
      />

      <div class="mt-8 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        <SurfaceCard>
          <h3 class="font-display text-lg">质量与返修</h3>
          <p class="mt-3 text-sm text-ink-muted">
            收到成品后请先按页面提示核对外观与结构。属于制作质量问题的脱胶、开线、结构松动，
            在约定保修期内由工作室负责处理，往返运费按责任方承担。
          </p>
        </SurfaceCard>
        <SurfaceCard>
          <h3 class="font-display text-lg">修改与加项</h3>
          <p class="mt-3 text-sm text-ink-muted">
            每单包含 {{ site.commissionPolicy.free_revision_count }} 次免费修改。重大加项会先生成变更单，
            写清新金额、延期天数和条款版本，你接受后才执行并生成单独补款。
          </p>
        </SurfaceCard>
        <SurfaceCard>
          <h3 class="font-display text-lg">取消与退款</h3>
          <p class="mt-3 text-sm text-ink-muted">
            取消规则按订单阶段与已确认条款处理，具体金额与已发生成本以合同说明页和订单页展示为准。
            退款走原支付渠道，不用星绒币替代现金退款。
          </p>
        </SurfaceCard>
      </div>

      <div class="mt-8 flex flex-wrap gap-3">
        <AppButton to="/commission/contract" variant="secondary">阅读合同说明与示例</AppButton>
        <AppButton to="/faq" variant="secondary">查看常见问题</AppButton>
        <AppButton to="/estimate">开始估价</AppButton>
      </div>
    </section>

    <!-- 配置加载失败：用骨架/空态说明，不隐藏入口 -->
    <section v-if="site.loading && !site.loaded" class="mt-12" aria-live="polite">
      <SkeletonBlock :lines="2" aria-label="正在读取站点配置" />
    </section>
    <section v-else-if="site.error" class="mt-12">
      <EmptyState
        tone="error"
        title="站点配置暂时无法加载"
        :description="`${site.error} 页面已使用文档基线值展示；数值以服务端配置为准，恢复后可重新加载。`"
      >
        <AppButton size="sm" @click="site.load(true)">重新加载配置</AppButton>
        <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
      </EmptyState>
    </section>
  </div>
</template>
