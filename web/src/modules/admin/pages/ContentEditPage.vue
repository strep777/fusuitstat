<script setup lang="ts">
/**
 * 内容编辑：草稿 → 预览 → 发布 / 下架；版本历史、slug、作者授权字段。
 *
 * 编辑带 `version` 乐观锁：服务端发现版本不一致返回 `VERSION_CONFLICT`，
 * 这里提示「内容已被他人更新，请刷新后重新提交」并给出刷新按钮。
 * 富文本用 textarea 输入 HTML 片段，服务端负责清洗。
 */
import { computed, onMounted, ref, watch } from "vue";
import { onBeforeRouteLeave, useRoute, useRouter } from "vue-router";

import {
  actionErrorMessage,
  adminApi,
  authorizationSummary,
  failureTone,
  text,
  usePageData,
  type AuthorAuthorization,
  type ContentRecord,
  type ContentVersion,
  type ContentWriteInput,
} from "@/modules/admin/api/admin";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, FieldList, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, EmptyState, FieldRow, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const auth = useAdminAuthStore();
const toast = useToast();

const isNew = computed(() => route.name === "admin-content-new");
const contentId = computed(() => (isNew.value ? "" : String(route.params.id ?? "")));

const form = ref({
  type: String(route.query.type ?? "news"),
  slug: "",
  title: "",
  summary: "",
  body_html: "",
  rights_holder: "",
  license_scope: "",
  authorization_note: "",
  consent_confirmed: false,
});

const version = ref(0);
const status = ref("DRAFT");
const dirty = ref(false);
const saveError = ref("");
const conflictNotice = ref("");
const versions = ref<ContentVersion[]>([]);
const versionsError = ref("");

const dialogOpen = ref(false);
const dialogMode = ref<"publish" | "archive">("publish");
const actionPending = ref(false);

const state = usePageData<ContentRecord | null>(
  async () => {
    if (isNew.value) return null;
    return adminApi.contentDetail(contentId.value);
  },
  null,
  "内容详情",
  () => auth.handleSessionExpired(),
);

const columns = computed(() => [
  { label: "标题", value: form.value.title },
  { label: "slug", value: form.value.slug },
  { label: "类型", value: form.value.type },
  { label: "状态", value: statusLabel.value },
  { label: "作者授权", value: authorizationSummary(form.value.consent_confirmed ? {
    rights_holder: form.value.rights_holder,
    license_scope: form.value.license_scope,
    authorization_note: form.value.authorization_note,
  } : null) },
  { label: "版本", value: version.value },
]);

const statusLabel = computed(() => {
  const map: Record<string, string> = { DRAFT: "草稿", PUBLISHED: "已发布", ARCHIVED: "已下架" };
  return map[status.value] ?? status.value;
});

const canSave = computed(() => form.value.title.trim().length > 0 && form.value.slug.trim().length > 0);

function fill(record: ContentRecord): void {
  form.value.type = String(record.type ?? "news");
  form.value.slug = String(record.slug ?? "");
  form.value.title = String(record.title ?? "");
  form.value.summary = String(record.summary ?? "");
  form.value.body_html = String(record.body_html ?? "");
  const authorization = (record.authorization ?? {}) as AuthorAuthorization;
  form.value.rights_holder = String(authorization.rights_holder ?? "");
  form.value.license_scope = String(authorization.license_scope ?? "");
  form.value.authorization_note = String(authorization.authorization_note ?? "");
  form.value.consent_confirmed = Boolean(authorization.consent_confirmed);
  version.value = typeof record.version === "number" ? record.version : 0;
  status.value = String(record.status ?? "DRAFT");
  dirty.value = false;
}

async function load(): Promise<void> {
  await state.load();
  if (state.data.value) fill(state.data.value);
  await loadVersions();
}

async function loadVersions(): Promise<void> {
  versionsError.value = "";
  if (isNew.value) {
    versions.value = [];
    return;
  }
  try {
    const payload = await adminApi.contentVersions(contentId.value);
    versions.value = payload.items;
  } catch {
    versions.value = [];
    versionsError.value = "版本历史接口尚未就绪或读取失败；这里不会显示伪造的历史记录。";
  }
}

function payload(): ContentWriteInput {
  return {
    type: form.value.type,
    slug: form.value.slug.trim(),
    title: form.value.title.trim(),
    summary: form.value.summary,
    body_html: form.value.body_html,
    authorization: {
      rights_holder: form.value.rights_holder,
      license_scope: form.value.license_scope,
      authorization_note: form.value.authorization_note,
      consent_confirmed: form.value.consent_confirmed,
    },
  };
}

async function save(): Promise<boolean> {
  saveError.value = "";
  conflictNotice.value = "";
  if (!canSave.value) {
    saveError.value = "标题与 slug 是必填项。";
    return false;
  }
  state.saving.value = true;
  try {
    if (isNew.value) {
      const created = await adminApi.createContent(payload());
      toast.success("草稿已创建", `《${created.title}》已保存为草稿，可以继续编辑或预览。`);
      await router.replace({ name: "admin-content-edit", params: { id: created.id } });
      return true;
    }
    const updated = await adminApi.updateContent(contentId.value, { ...payload(), version: version.value });
    fill(updated);
    toast.success("草稿已保存", "保存不会影响线上页面；发布后才会对外可见。");
    return true;
  } catch (caught) {
    const view = actionErrorMessage(caught, "保存没有成功，请稍后重试。");
    saveError.value = view.message;
    if (view.versionConflict) conflictNotice.value = view.message;
    return false;
  } finally {
    state.saving.value = false;
  }
}

/** 保存后用最新数据重新打开表单；服务端返回的 version 是唯一权威。 */
async function refreshAfterConflict(): Promise<void> {
  conflictNotice.value = "";
  saveError.value = "";
  await load();
  toast.info("已刷新", "表单内容已替换为服务端最新版本，请重新确认后再提交。");
}

function openDialog(mode: "publish" | "archive"): void {
  dialogMode.value = mode;
  dialogOpen.value = true;
  saveError.value = "";
}

async function runAction(payloadValue: { reason: string }): Promise<void> {
  actionPending.value = true;
  try {
    const saved = await save();
    if (!saved) {
      dialogOpen.value = false;
      return;
    }
    if (dialogMode.value === "publish") {
      const result = await adminApi.publishContent(contentId.value, { version: version.value, reason: payloadValue.reason });
      fill(result);
      toast.success("已发布", "受控预渲染任务会校验并原子替换静态页面。");
    } else {
      const result = await adminApi.archiveContent(contentId.value, { version: version.value, reason: payloadValue.reason });
      fill(result);
      toast.success("已下架", "公开页面已撤出，历史版本仍然保留。");
    }
    dialogOpen.value = false;
    await loadVersions();
  } catch (caught) {
    const view = actionErrorMessage(caught, "操作没有成功，请稍后重试。");
    saveError.value = view.message;
    if (view.versionConflict) conflictNotice.value = view.message;
    dialogOpen.value = false;
  } finally {
    actionPending.value = false;
  }
}

watch(
  () => [form.value.title, form.value.slug, form.value.summary, form.value.body_html],
  () => {
    dirty.value = true;
  },
);

onBeforeRouteLeave(() => {
  if (!dirty.value) return true;
  return window.confirm("有未保存的修改，离开后需要重新填写。确定离开吗？");
});

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      :eyebrow="isNew ? 'Content · New' : 'Content · Edit'"
      :title="isNew ? '新建内容草稿' : `编辑内容：${text(form.title, '未命名')}`"
      description="草稿、预览与发布是分开的动作：保存只写草稿，发布才会重建公开静态页面。富文本以 HTML 片段提交，服务端负责清洗。"
    >
      <template #actions>
        <StatusBadge :tone="status === 'PUBLISHED' ? 'jade' : status === 'ARCHIVED' ? 'neutral' : 'amber'" :label="statusLabel" />
        <StatusBadge tone="neutral" :label="`版本 ${version}`" />
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      v-if="conflictNotice"
      tone="danger"
      title="内容已被他人更新"
      :description="`${conflictNotice} 你的改动还在下面的表单里，但直接提交会被拒绝；请先刷新取得最新版本，再重新提交。`"
    >
      <AppButton size="sm" variant="danger" @click="refreshAfterConflict">刷新并载入最新版本</AppButton>
    </InlineNotice>

    <div v-if="state.loading.value" class="space-y-4">
      <SkeletonBlock :lines="6" aria-label="正在加载内容详情" />
    </div>

    <EmptyState
      v-else-if="state.error.value"
      :tone="failureTone(state.error.value)"
      :title="state.error.value.title"
      :description="`${state.error.value.description} ${state.error.value.retryHint}`"
    >
      <AppButton size="sm" @click="load">重试</AppButton>
      <AppButton size="sm" variant="secondary" to="/admin/content">返回内容列表</AppButton>
    </EmptyState>

    <div v-else class="grid gap-6 xl:grid-cols-[1.5fr_1fr]">
      <div class="space-y-6">
        <SurfaceCard>
          <SectionHeading level="2" title="正文与元数据" description="slug 决定公开地址，改动后旧地址会失效；标题与摘要会用于列表和分享卡片。" />
          <form class="mt-5 space-y-4" @submit.prevent="save">
            <div class="grid gap-4 sm:grid-cols-2">
              <FieldRow label="类型" required for="content-type">
                <select id="content-type" v-model="form.type" class="field-base">
                  <option value="work">作品</option>
                  <option value="news">公告</option>
                  <option value="series">系列</option>
                  <option value="faq">FAQ</option>
                  <option value="team">团队</option>
                  <option value="legal">法律文本</option>
                </select>
              </FieldRow>
              <FieldRow label="slug" required for="content-slug" hint="只允许小写字母、数字与连字符">
                <input id="content-slug" v-model="form.slug" type="text" class="field-base font-mono" placeholder="2026-spring-schedule" />
              </FieldRow>
            </div>

            <FieldRow label="标题" required for="content-title">
              <input id="content-title" v-model="form.title" type="text" class="field-base" maxlength="120" />
            </FieldRow>

            <FieldRow label="摘要" for="content-summary" hint="用于列表与分享卡片，建议 40–90 字">
              <textarea id="content-summary" v-model="form.summary" rows="2" class="field-base resize-y" maxlength="300" />
            </FieldRow>

            <FieldRow
              label="正文 HTML 片段"
              for="content-body"
              hint="可以直接输入 HTML；服务端保存前会移除脚本、内联事件与不允许的标签，公开页只渲染清洗后的结果。"
            >
              <textarea
                id="content-body"
                v-model="form.body_html"
                rows="14"
                class="field-base font-mono text-xs"
                spellcheck="false"
                placeholder="<p>正文段落</p>"
              />
            </FieldRow>

            <fieldset class="rounded-md border border-line p-4">
              <legend class="px-1 text-sm font-medium text-ink-soft">作者授权</legend>
              <p class="mt-1 text-xs text-ink-faint">
                只发布已取得公开展示授权的内容。这里登记权利人与授权范围，供后续核对；不填写联系方式。
              </p>
              <div class="mt-4 grid gap-4 sm:grid-cols-2">
                <FieldRow label="权利人 / 作者" for="content-rights">
                  <input id="content-rights" v-model="form.rights_holder" type="text" class="field-base" />
                </FieldRow>
                <FieldRow label="授权范围" for="content-license" hint="例如：官网与社交媒体展示，可署名，不得转售">
                  <input id="content-license" v-model="form.license_scope" type="text" class="field-base" />
                </FieldRow>
              </div>
              <FieldRow class="mt-4" label="授权备注" for="content-auth-note">
                <textarea id="content-auth-note" v-model="form.authorization_note" rows="2" class="field-base resize-y" />
              </FieldRow>
              <label class="mt-4 flex items-start gap-3 text-sm">
                <input v-model="form.consent_confirmed" type="checkbox" class="mt-1 size-5" />
                <span>已确认取得公开展示授权，并保留授权凭据。</span>
              </label>
            </fieldset>

            <p v-if="saveError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
              {{ saveError }}
            </p>

            <div class="flex flex-wrap items-center gap-3">
              <AppButton type="submit" :loading="state.saving.value" :disabled="!canSave" :disabled-reason="canSave ? '' : '标题与 slug 必须先填写'">
                保存草稿
              </AppButton>
              <PermissionGate :code="ADMIN_PERMISSIONS.contentPublish" :show-missing="false">
                <AppButton
                  v-if="!isNew"
                  variant="secondary"
                  :disabled="dirty"
                  :disabled-reason="dirty ? '有未保存的修改，请先保存草稿再发布' : ''"
                  @click="openDialog('publish')"
                >
                  发布
                </AppButton>
                <AppButton
                  v-if="!isNew && status === 'PUBLISHED'"
                  variant="danger"
                  :disabled="dirty"
                  :disabled-reason="dirty ? '有未保存的修改，请先保存草稿再下架' : ''"
                  @click="openDialog('archive')"
                >
                  下架
                </AppButton>
              </PermissionGate>
              <p v-if="dirty" class="text-xs text-ink-faint">有未保存的修改，发布前请先保存。</p>
            </div>
          </form>
        </SurfaceCard>

        <SurfaceCard>
          <SectionHeading
            level="2"
            title="发布前自查"
            description="为避免把未清洗的 HTML 直接执行，后台不渲染正文预览，只显示片段字符数；公开效果以服务端清洗后的预览环境为准。"
          />
          <div class="mt-4 rounded-md border border-line bg-canvas p-5">
            <FieldList :items="columns" />
            <div class="mt-4 border-t border-line pt-4">
              <p class="text-xs text-ink-faint">
                正文片段 {{ form.body_html.length }} 个字符。服务端保存时会移除脚本、内联事件与不允许的标签，
                公开页面只渲染清洗后的结果。
              </p>
              <pre class="mt-2 max-h-64 overflow-auto rounded-xs border border-line bg-canvas-sunk p-3 font-mono text-xs whitespace-pre-wrap">{{ form.body_html || '（正文还是空的）' }}</pre>
            </div>
          </div>
        </SurfaceCard>
      </div>

      <div class="space-y-6">
        <SurfaceCard>
          <SectionHeading level="2" title="版本历史" description="每次保存都会留下版本；冲突时以服务端版本号为准。" />
          <p v-if="versionsError" class="mt-4 rounded-md border border-line bg-surface-muted px-4 py-3 text-sm text-ink-muted">
            {{ versionsError }}
          </p>
          <ol v-else-if="versions.length" class="mt-4 space-y-3">
            <li v-for="item in versions" :key="item.version" class="rounded-sm border border-line px-4 py-3 text-sm">
              <div class="flex items-center justify-between gap-3">
                <span class="font-medium">版本 {{ item.version }}</span>
                <StatusBadge :tone="item.status === 'PUBLISHED' ? 'jade' : 'neutral'" :label="text(item.status, '草稿')" />
              </div>
              <p class="mt-1 text-xs text-ink-faint">
                {{ text(item.editor, "未知员工") }} · {{ formatDateTime(item.created_at) }}
              </p>
              <p v-if="item.note" class="mt-1 text-xs text-ink-muted">{{ item.note }}</p>
            </li>
          </ol>
          <p v-else class="mt-4 text-sm text-ink-muted">还没有历史版本记录。</p>
        </SurfaceCard>

        <SurfaceCard>
          <SectionHeading level="2" title="提交前检查" description="发布前请逐条确认，避免把未授权或未扫描的素材公开。" />
          <ul class="mt-4 space-y-2 text-sm text-ink-muted">
            <li>· slug 是否与既有页面冲突（冲突时服务端会拒绝）。</li>
            <li>· 正文里的图片是否都已完成扫描并登记授权。</li>
            <li>· 摘要是否与正文一致，不含未经核准的交期承诺。</li>
            <li>· 法律文本与规则版本号是否需要同步更新。</li>
          </ul>
        </SurfaceCard>
      </div>
    </div>

    <ConfirmDialog
      v-model="dialogOpen"
      :title="dialogMode === 'publish' ? '发布内容到公开站点' : '下架内容并撤出公开站点'"
      :target="`《${text(form.title, '未命名')}》 · ${text(form.slug)}`"
      :change="dialogMode === 'publish' ? '状态 → 已发布；公开站点出现这条内容。' : '状态 → 已下架；公开页面被移除。'"
      :consequence="
        dialogMode === 'publish'
          ? '发布会先保存草稿，再触发生成受控预渲染任务，校验并原子替换静态页面；发布后对外可见。'
          : '下架会让读者立刻看不到这条内容；历史版本仍保留，重新发布需要再次确认。'
      "
      :amount="`版本 ${version}`"
      :confirm-label="dialogMode === 'publish' ? '确认发布' : '确认下架'"
      :tone="dialogMode === 'publish' ? 'primary' : 'danger'"
      :pending="actionPending"
      :require-typed="form.slug"
      typed-label="为防止误操作，请输入这条内容的 slug"
      @confirm="runAction"
    />
  </div>
</template>
