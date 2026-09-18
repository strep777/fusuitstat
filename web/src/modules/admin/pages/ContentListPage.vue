<script setup lang="ts">
/**
 * 内容列表：作品、公告、系列、FAQ、团队、法律文本。
 *
 * 约束：草稿/预览/发布/下架分开动作；下架与发布都要走确认对话框（显示具体对象与后果）；
 * 过滤条件进 URL；每个数据区域都有 loading / empty / error / forbidden / not-found。
 */
import { computed, onMounted, ref } from "vue";
import { RouterLink, useRoute, useRouter } from "vue-router";

import {
  actionErrorMessage,
  adminApi,
  authorizationSummary,
  text,
  usePageData,
  type AdminList,
  type ContentRecord,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import PermissionGate from "@/modules/admin/components/PermissionGate.vue";
import { AdminPageHeader, InlineNotice } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { ADMIN_PERMISSIONS, useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const auth = useAdminAuthStore();
const toast = useToast();

const CONTENT_TYPES = [
  { value: "", label: "全部类型" },
  { value: "work", label: "作品" },
  { value: "news", label: "公告" },
  { value: "series", label: "系列" },
  { value: "faq", label: "FAQ" },
  { value: "team", label: "团队" },
  { value: "legal", label: "法律文本" },
];

const STATUS_OPTIONS = [
  { value: "", label: "全部状态" },
  { value: "DRAFT", label: "草稿" },
  { value: "PUBLISHED", label: "已发布" },
  { value: "ARCHIVED", label: "已下架" },
];

const state = usePageData<AdminList<ContentRecord>>(
  () =>
    adminApi.content({
      type: typeFilter.value || undefined,
      status: statusFilter.value || undefined,
      q: search.value || undefined,
      cursor: cursors.value[cursors.value.length - 1] || undefined,
      limit: 20,
    }),
  { items: [] },
  "内容列表",
  () => auth.handleSessionExpired(),
);

const search = ref(String(route.query.q ?? ""));
const typeFilter = ref(String(route.query.type ?? ""));
const statusFilter = ref(String(route.query.status ?? ""));
const page = ref(1);
const cursors = ref<string[]>([]);
const rows = computed(() => state.data.value.items);

const columns: TableColumn[] = [
  { key: "title", label: "标题" },
  { key: "type", label: "类型", width: "110px" },
  { key: "slug", label: "slug", width: "180px", hideOnMobile: true },
  { key: "status", label: "状态", width: "110px" },
  { key: "authorization", label: "作者授权", hideOnMobile: true },
  { key: "updated_at", label: "最近更新", width: "170px", hideOnMobile: true },
];

const target = ref<ContentRecord | null>(null);
const dialogOpen = ref(false);
const dialogMode = ref<"publish" | "archive">("publish");
const actionError = ref("");
const pending = ref(false);

const dialogTitle = computed(() =>
  dialogMode.value === "publish" ? "发布内容到公开站点" : "下架内容并撤出公开站点",
);
const dialogChange = computed(() => {
  if (!target.value) return "";
  return dialogMode.value === "publish"
    ? "状态：草稿 / 已下架 → 已发布；公开站点将出现这条内容。"
    : "状态：已发布 → 已下架；公开页面将被移除，静态页面重新生成。";
});
const dialogConsequence = computed(() =>
  dialogMode.value === "publish"
    ? "发布会触发受控预渲染任务，校验并原子替换静态页面；发布后对外可见，需先确认作者授权与素材扫描状态。"
    : "下架会让读者立刻看不到内容，但不删除历史版本；重新发布需要再次确认。",
);

async function applyQuery(): Promise<void> {
  await router.replace({
    query: {
      ...(typeFilter.value ? { type: typeFilter.value } : {}),
      ...(statusFilter.value ? { status: statusFilter.value } : {}),
      ...(search.value ? { q: search.value } : {}),
    },
  });
  page.value = 1;
  cursors.value = [];
  await state.load();
}

async function nextPage(): Promise<void> {
  const cursor = state.data.value.next_cursor;
  if (!cursor) return;
  cursors.value = [...cursors.value, cursor];
  page.value += 1;
  await state.load();
}

async function resetAndLoad(): Promise<void> {
  page.value = 1;
  cursors.value = [];
  await state.load();
}

async function prevPage(): Promise<void> {
  if (cursors.value.length <= 1) {
    cursors.value = [];
    page.value = 1;
  } else {
    cursors.value = cursors.value.slice(0, -1);
    page.value -= 1;
  }
  await state.load();
}

function openDialog(row: ContentRecord, mode: "publish" | "archive"): void {
  target.value = row;
  dialogMode.value = mode;
  actionError.value = "";
  dialogOpen.value = true;
}

async function runAction(payload: { reason: string }): Promise<void> {
  const row = target.value;
  if (!row) return;
  pending.value = true;
  actionError.value = "";
  try {
    const version = typeof row.version === "number" ? row.version : 0;
    if (dialogMode.value === "publish") {
      await adminApi.publishContent(row.id, { version, reason: payload.reason });
      toast.success("已发布", `《${row.title}》已发布；公开站点会在静态页面重建后更新。`);
    } else {
      await adminApi.archiveContent(row.id, { version, reason: payload.reason });
      toast.success("已下架", `《${row.title}》已下架，历史版本仍保留在版本历史中。`);
    }
    dialogOpen.value = false;
    await state.load();
  } catch (caught) {
    const view = actionErrorMessage(caught, "操作没有成功，请稍后重试。");
    actionError.value = view.message;
    if (!view.versionConflict) dialogOpen.value = false;
  } finally {
    pending.value = false;
  }
}

onMounted(state.load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Content"
      title="内容管理"
      description="作品、公告、系列、FAQ、团队与法律文本都在这里维护：先存草稿，预览无误后再发布；下架不删除历史版本。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="state.load()">刷新列表</AppButton>
        <PermissionGate :code="ADMIN_PERMISSIONS.contentWrite">
          <AppButton size="sm" :to="`/admin/content/new${route.query.type ? `?type=${route.query.type}` : ''}`">
            新建内容
          </AppButton>
        </PermissionGate>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="富文本以 HTML 片段提交，由服务端清洗后再入库"
      description="编辑器里输入的是 HTML 片段；保存后服务端会移除脚本、内联事件与不允许的标签。不要粘贴外部页面的整段 HTML。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="applyQuery">
      <FieldRow label="关键词" for="content-q" hint="按标题或 slug 匹配">
        <input id="content-q" v-model="search" type="search" class="field-base" placeholder="例如 展会 或 fursuit-care" />
      </FieldRow>
      <FieldRow label="类型" for="content-type">
        <select id="content-type" v-model="typeFilter" class="field-base" @change="applyQuery">
          <option v-for="item in CONTENT_TYPES" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </FieldRow>
      <FieldRow label="状态" for="content-status">
        <select id="content-status" v-model="statusFilter" class="field-base" @change="applyQuery">
          <option v-for="item in STATUS_OPTIONS" :key="item.value" :value="item.value">{{ item.label }}</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3 sm:col-span-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; resetAndLoad()">清空关键词</AppButton>
        <p class="text-xs text-ink-faint">筛选条件会写进地址栏，方便把同一条链接发给同事复核。</p>
      </div>
    </form>

    <p v-if="actionError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
      {{ actionError }}
    </p>

    <DataTable
      :columns="columns"
      :rows="rows"
      :loading="state.loading.value"
      :error="state.error.value"
      :page="page"
      :next-cursor="state.data.value.next_cursor"
      :has-more="Boolean(state.data.value.has_more)"
      :can-prev="page > 1"
      empty-title="还没有符合条件的内容"
      empty-description="可以调整类型或状态筛选，也可以先新建一条草稿。"
      @retry="state.load()"
      @next="nextPage"
      @prev="prevPage"
    >
      <template #cell-title="{ row }">
        <RouterLink :to="`/admin/content/${row.id}`" class="link-underline font-medium hover:text-amber-strong">
          {{ text(row.title) }}
        </RouterLink>
        <p class="mt-1 text-xs text-ink-faint">{{ text(row.summary, "未填写摘要") }}</p>
      </template>
      <template #cell-slug="{ row }">
        <span class="font-mono text-xs">{{ text(row.slug) }}</span>
      </template>
      <template #cell-authorization="{ row }">
        <span class="text-xs">{{ authorizationSummary(row.authorization) }}</span>
      </template>
      <template #cell-updated_at="{ row }">
        <span class="tabular-nums text-xs">{{ formatDateTime(row.updated_at) }}</span>
      </template>
      <template #actions="{ row }">
        <AppButton size="sm" variant="secondary" :to="`/admin/content/${row.id}`">编辑</AppButton>
        <PermissionGate :code="ADMIN_PERMISSIONS.contentPublish" :show-missing="false">
          <AppButton
            v-if="row.status !== 'PUBLISHED'"
            size="sm"
            @click="openDialog(row, 'publish')"
          >
            发布
          </AppButton>
          <AppButton v-else size="sm" variant="danger" @click="openDialog(row, 'archive')">下架</AppButton>
        </PermissionGate>
      </template>
    </DataTable>

    <p class="text-xs text-ink-faint">
      发布与下架需要 <span class="font-mono">content.publish</span> 权限码；只有
      <span class="font-mono">content.write</span> 的员工可以编辑草稿，但服务端会拒绝其发布请求。
    </p>

    <ConfirmDialog
      v-model="dialogOpen"
      :title="dialogTitle"
      :target="target ? `《${target.title}》 · ${target.slug}` : ''"
      :change="dialogChange"
      :consequence="`${dialogConsequence}${actionError ? ` 上一次提交失败：${actionError}` : ''}`"
      :confirm-label="dialogMode === 'publish' ? '确认发布这条内容' : '确认下架这条内容'"
      :tone="dialogMode === 'publish' ? 'primary' : 'danger'"
      :pending="pending"
      reason-label="操作理由（会写入审计记录）"
      @confirm="runAction"
    />
  </div>
</template>
