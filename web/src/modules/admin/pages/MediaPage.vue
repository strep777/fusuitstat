<script setup lang="ts">
/**
 * 素材库：授权信息、扫描状态、替代文本、引用关系。
 *
 * 规则（05/11 文档）：扫描完成前素材不可被引用；替代文本描述角色与角度，纯装饰留空。
 * 这里只显示文件名与授权摘要，不显示对象存储的签名 URL。
 */
import { computed, onMounted, ref } from "vue";

import {
  actionErrorMessage,
  adminApi,
  authorizationSummary,
  collect,
  describeFailure,
  text,
  usePageData,
  type AdminList,
  type FailureView,
  type MediaRecord,
  type StorageStatus,
} from "@/modules/admin/api/admin";
import DataTable from "@/modules/admin/components/DataTable.vue";
import type { TableColumn } from "@/modules/admin/components/DataTable.vue";
import ConfirmDialog from "@/modules/admin/components/ConfirmDialog.vue";
import { AdminPageHeader, FieldList, InlineNotice, ModuleCard } from "@/modules/admin/pages/AdminNotFoundPage.vue";
import { useAdminAuthStore } from "@/modules/admin/stores/adminAuth";
import { AppButton, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { formatDateTime } from "@/shared/lib/format";
import { useToast } from "@/shared/lib/toast";

const auth = useAdminAuthStore();
const toast = useToast();

const state = usePageData<AdminList<MediaRecord>>(
  () => adminApi.media({ scan_status: scanFilter.value || undefined, q: search.value || undefined, limit: 20 }),
  { items: [] },
  "素材列表",
  () => auth.handleSessionExpired(),
);

const scanFilter = ref("");
const search = ref("");
const storage = ref<StorageStatus | null>(null);
const storageError = ref<FailureView | null>(null);

const columns: TableColumn[] = [
  { key: "filename", label: "文件名" },
  { key: "scan_status", label: "扫描状态", width: "120px" },
  { key: "alt_text", label: "替代文本" },
  { key: "authorization", label: "授权信息", hideOnMobile: true },
  { key: "ref_count", label: "引用", width: "90px" },
  { key: "created_at", label: "上传时间", width: "170px", hideOnMobile: true },
];

const editTarget = ref<MediaRecord | null>(null);
const linkTarget = ref<MediaRecord | null>(null);
const cleanupOpen = ref(false);
const dialogError = ref("");
const pending = ref(false);

const editForm = ref({ alt_text: "", rights_holder: "", license_scope: "", authorization_note: "" });
const linkForm = ref({ target_type: "content", target_id: "", note: "" });

async function load(): Promise<void> {
  await state.load();
  const result = await collect<{ storage: StorageStatus }>({ storage: () => adminApi.mediaStorageStatus() });
  if (result.ok.storage) {
    storage.value = result.ok.storage;
    storageError.value = null;
  } else {
    storage.value = null;
    const failure = result.failed.find((item) => item.key === "storage");
    storageError.value = describeFailure(failure?.error, "存储状态");
  }
}

async function applyFilters(): Promise<void> {
  await state.load();
}

function openEdit(row: MediaRecord): void {
  editTarget.value = row;
  const authorization = (row.authorization ?? {}) as Record<string, unknown>;
  editForm.value = {
    alt_text: String(row.alt_text ?? ""),
    rights_holder: String(authorization.rights_holder ?? ""),
    license_scope: String(authorization.license_scope ?? ""),
    authorization_note: String(authorization.authorization_note ?? ""),
  };
  dialogError.value = "";
}

async function saveEdit(payload: { reason: string }): Promise<void> {
  const row = editTarget.value;
  if (!row) return;
  pending.value = true;
  dialogError.value = "";
  try {
    await adminApi.updateMedia(row.id, {
      alt_text: editForm.value.alt_text,
      authorization: {
        rights_holder: editForm.value.rights_holder,
        license_scope: editForm.value.license_scope,
        authorization_note: `${editForm.value.authorization_note}${payload.reason ? `（理由：${payload.reason}）` : ""}`,
      },
    });
    toast.success("素材信息已更新", "替代文本与授权信息已写入；引用关系不改动。");
    editTarget.value = null;
    await state.load();
  } catch (caught) {
    dialogError.value = actionErrorMessage(caught, "保存失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

async function saveLink(payload: { reason: string }): Promise<void> {
  const row = linkTarget.value;
  if (!row) return;
  pending.value = true;
  dialogError.value = "";
  try {
    await adminApi.linkMedia(row.id, {
      target_type: linkForm.value.target_type,
      target_id: linkForm.value.target_id.trim(),
      note: payload.reason,
    });
    toast.success("已建立引用关系", "手工引用会同时写入审计记录。");
    linkTarget.value = null;
    await state.load();
  } catch (caught) {
    dialogError.value = actionErrorMessage(caught, "建立引用失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

/** 第一步永远是预演：只统计无引用素材数量，正式删除需要再次确认。 */
async function runCleanup(): Promise<void> {
  pending.value = true;
  dialogError.value = "";
  try {
    const result = await adminApi.cleanupMedia({ dry_run: true, older_than_days: 30 });
    const count = typeof result.orphan_count === "number" ? result.orphan_count : null;
    toast.success(
      "已完成清理预演",
      count === null ? "接口未返回孤儿素材数量。" : `发现 ${count} 个无引用素材；正式删除需要再次确认。`,
    );
    cleanupOpen.value = false;
  } catch (caught) {
    dialogError.value = actionErrorMessage(caught, "清理失败，请稍后重试。").message;
  } finally {
    pending.value = false;
  }
}

const scanTone = (value: unknown): "jade" | "amber" | "wine" | "neutral" => {
  const status = String(value ?? "");
  if (status === "READY" || status === "CLEAN") return "jade";
  if (status === "PENDING" || status === "SCANNING") return "amber";
  if (status === "INFECTED" || status === "FAILED") return "wine";
  return "neutral";
};

const storageItems = computed(() => [
  { label: "存储就绪", value: storage.value ? (storage.value.ready ? "是" : "否") : "未就绪" },
  { label: "已用容量", value: storage.value?.used_bytes != null ? `${Math.round(storage.value.used_bytes / 1024 / 1024)} MiB` : "—" },
  { label: "无引用素材", value: storage.value?.orphan_count ?? "—" },
  { label: "说明", value: storage.value?.detail ?? "—" },
]);

onMounted(load);
</script>

<template>
  <div class="space-y-6">
    <AdminPageHeader
      eyebrow="Media"
      title="素材库"
      description="登记授权信息与替代文本、核对扫描状态与引用关系。扫描未完成的素材不能出现在公开页面；后台不显示对象存储的签名地址。"
    >
      <template #actions>
        <AppButton size="sm" variant="secondary" :loading="state.loading.value" @click="load">刷新</AppButton>
        <AppButton size="sm" variant="danger" @click="cleanupOpen = true">清理无引用素材</AppButton>
      </template>
    </AdminPageHeader>

    <InlineNotice
      title="扫描完成前不可引用"
      description="上传只接受 JPEG / PNG / WebP，服务端会校验魔数与实际大小，安全解码并移除 EXIF 后再生成派生图。这里只展示状态，不提供绕过扫描的入口。"
    />

    <form class="grid gap-4 rounded-lg border border-line bg-surface p-5 sm:grid-cols-3" @submit.prevent="applyFilters">
      <FieldRow label="文件名关键词" for="media-q">
        <input id="media-q" v-model="search" type="search" class="field-base" placeholder="例如 tail-front" />
      </FieldRow>
      <FieldRow label="扫描状态" for="media-scan">
        <select id="media-scan" v-model="scanFilter" class="field-base">
          <option value="">全部状态</option>
          <option value="READY">已完成</option>
          <option value="PENDING">排队中</option>
          <option value="SCANNING">扫描中</option>
          <option value="FAILED">失败</option>
        </select>
      </FieldRow>
      <div class="flex items-end gap-3">
        <AppButton type="submit" size="sm">应用筛选</AppButton>
        <AppButton size="sm" variant="quiet" @click="search = ''; scanFilter = ''; applyFilters()">清空</AppButton>
      </div>
    </form>

    <div class="grid gap-6 lg:grid-cols-[1.6fr_1fr]">
      <div class="space-y-4">
        <DataTable
          :columns="columns"
          :rows="state.data.value.items"
          :loading="state.loading.value"
          :error="state.error.value"
          :has-more="Boolean(state.data.value.has_more)"
          empty-title="素材库还是空的"
          empty-description="上传素材后需要先完成扫描与授权登记，才会出现在这里。"
          @retry="load"
        >
          <template #cell-filename="{ row }">
            <span class="font-mono text-xs">{{ text(row.filename, "未命名素材") }}</span>
          </template>
          <template #cell-scan_status="{ row }">
            <StatusBadge :tone="scanTone(row.scan_status)" :label="text(row.scan_status, '未知')" />
          </template>
          <template #cell-alt_text="{ row }">
            <span :class="row.alt_text ? 'text-ink-soft' : 'text-wine'">
              {{ text(row.alt_text, "缺少替代文本（装饰图请显式标注为装饰）") }}
            </span>
          </template>
          <template #cell-authorization="{ row }">
            <span class="text-xs">{{ authorizationSummary(row.authorization) }}</span>
          </template>
          <template #cell-ref_count="{ row }">
            <span class="tabular-nums">{{ text(row.ref_count, "0") }}</span>
          </template>
          <template #cell-created_at="{ row }">
            <span class="tabular-nums text-xs">{{ formatDateTime(row.created_at) }}</span>
          </template>
          <template #actions="{ row }">
            <AppButton size="sm" variant="secondary" @click="openEdit(row)">编辑授权与替代文本</AppButton>
            <AppButton size="sm" variant="quiet" @click="linkTarget = row; linkForm.target_id = ''">手工建立引用</AppButton>
          </template>
        </DataTable>

        <SurfaceCard v-if="state.data.value.items.length">
          <h2 class="font-display text-lg">引用关系</h2>
          <p class="mt-1 text-xs text-ink-faint">只列出当前页素材的引用，避免一次拉取整库关系。</p>
          <ul class="mt-4 space-y-3 text-sm">
            <li v-for="row in state.data.value.items" :key="row.id" class="rounded-sm border border-line px-4 py-3">
              <p class="font-mono text-xs">{{ text(row.filename) }}</p>
              <ul v-if="row.references?.length" class="mt-2 space-y-1 text-xs text-ink-muted">
                <li v-for="(ref, index) in row.references" :key="index">
                  · {{ text(ref.kind, "引用") }}：{{ text(ref.label, "未命名对象") }}
                </li>
              </ul>
              <p v-else class="mt-2 text-xs text-ink-faint">当前没有引用。可以清理，或手工建立引用。</p>
            </li>
          </ul>
        </SurfaceCard>
      </div>

      <div class="space-y-6">
        <ModuleCard title="存储状态" :status="storage ? '实时' : '未就绪'">
          <div v-if="storageError" class="space-y-3">
            <p class="text-sm text-ink-muted">{{ storageError.description }}</p>
            <AppButton size="sm" variant="secondary" @click="load">重试</AppButton>
          </div>
          <FieldList v-else :items="storageItems" :columns="1" />
        </ModuleCard>

        <SurfaceCard>
          <h2 class="font-display text-lg">替代文本规则</h2>
          <ul class="mt-3 space-y-2 text-sm text-ink-muted">
            <li>· 描述角色物种与拍摄角度，例如「龙形全装正面，展示头部与手套」。</li>
            <li>· 装饰性纹理与分隔图显式留空，不写「图片」这类无信息文本。</li>
            <li>· 不写客户真实姓名、地址或联系方式。</li>
          </ul>
        </SurfaceCard>
      </div>
    </div>

    <!-- 编辑授权与替代文本（就地编辑，不用危险动作对话框） -->
    <SurfaceCard v-if="editTarget" class="border-amber">
      <h2 class="font-display text-lg">编辑素材：{{ text(editTarget.filename) }}</h2>
      <div class="mt-4 space-y-4">
        <FieldRow label="替代文本" for="media-alt" hint="装饰图可以明确写「装饰图，无替代文本」">
          <input id="media-alt" v-model="editForm.alt_text" type="text" class="field-base" maxlength="160" />
        </FieldRow>
        <div class="grid gap-4 sm:grid-cols-2">
          <FieldRow label="权利人 / 作者" for="media-rights">
            <input id="media-rights" v-model="editForm.rights_holder" type="text" class="field-base" />
          </FieldRow>
          <FieldRow label="授权范围" for="media-license">
            <input id="media-license" v-model="editForm.license_scope" type="text" class="field-base" />
          </FieldRow>
        </div>
        <FieldRow label="授权备注" for="media-note">
          <textarea id="media-note" v-model="editForm.authorization_note" rows="2" class="field-base resize-y" />
        </FieldRow>
        <p v-if="dialogError" class="rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
          {{ dialogError }}
        </p>
        <div class="flex flex-wrap gap-3">
          <AppButton :loading="pending" @click="saveEdit({ reason: '更新素材替代文本与授权登记' })">保存素材信息</AppButton>
          <AppButton variant="quiet" @click="editTarget = null">取消</AppButton>
        </div>
      </div>
    </SurfaceCard>

    <!-- 手工建立引用 -->
    <SurfaceCard v-if="linkTarget" class="border-amber">
      <h2 class="font-display text-lg">手工建立引用：{{ text(linkTarget.filename) }}</h2>
      <p class="mt-1 text-xs text-ink-faint">
        只在自动引用关系缺失时使用；引用关系决定素材能否被清理，请填写真实的对象编号。
      </p>
      <div class="mt-4 grid gap-4 sm:grid-cols-2">
        <FieldRow label="引用类型" for="link-type">
          <select id="link-type" v-model="linkForm.target_type" class="field-base">
            <option value="content">内容</option>
            <option value="product">商品</option>
            <option value="character">角色</option>
            <option value="work">作品</option>
          </select>
        </FieldRow>
        <FieldRow label="目标对象编号" for="link-id" hint="例如内容 ID 或商品 ID">
          <input id="link-id" v-model="linkForm.target_id" type="text" class="field-base font-mono" />
        </FieldRow>
      </div>
      <p v-if="dialogError" class="mt-4 rounded-md border border-wine bg-wine-soft px-4 py-3 text-sm text-wine" role="alert">
        {{ dialogError }}
      </p>
      <div class="mt-4 flex flex-wrap gap-3">
        <AppButton
          :loading="pending"
          :disabled="!linkForm.target_id.trim()"
          :disabled-reason="linkForm.target_id.trim() ? '' : '请先填写目标对象编号'"
          @click="saveLink({ reason: '后台手工建立素材引用' })"
        >
          建立引用
        </AppButton>
        <AppButton variant="quiet" @click="linkTarget = null">取消</AppButton>
      </div>
    </SurfaceCard>

    <ConfirmDialog
      v-model="cleanupOpen"
      title="清理无引用素材（先执行预演）"
      target="素材库中 30 天以上、没有任何引用关系的素材"
      change="第一步只做预演并返回数量；正式删除需要另行确认。"
      consequence="预演不会删除任何文件。正式删除会移除派生图与原件，且无法恢复；被引用的素材不会进入清理列表。"
      confirm-label="执行清理预演"
      tone="danger"
      :pending="pending"
      require-typed="清理预演"
      typed-label="请输入「清理预演」确认"
      @confirm="runCleanup"
    />
  </div>
</template>
