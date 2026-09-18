<script setup lang="ts">
/**
 * 分步询价。
 *
 * 规则源：文档/07（询价表单、失败与恢复）、文档/11（页面布局基线、表单状态）。
 *
 * 关键约束：
 * - 步序固定：委托范围 → 设定图与角色 → 制作需求 → 联系偏好 → 预览与同意 → 提交结果。
 * - 草稿保存在服务端；本地只记 draft_id，刷新后按 id 恢复，不把敏感内容写进 localStorage。
 * - 首次询价不收集生日、性别、住址；量体信息在接单后单独收集。
 * - 设定图 1–3 张、单张 ≤10MiB、仅 JPEG/PNG/WebP；上传后必须 complete 且 scan_status=ready。
 * - 提交按钮在提交中与提交成功后都不可再次触发；重复请求由幂等键兜底。
 */
import { computed, onBeforeUnmount, onMounted, reactive, ref, watch } from "vue";
import { useRoute, useRouter } from "vue-router";

import { fetchSeriesList, type SeriesDetail } from "@/modules/content/api/content";
import { useAuthStore } from "@/modules/identity/stores/auth";
import { useSiteStore } from "@/modules/content/stores/site";
import {
  CONTACT_PREFERENCE_OPTIONS,
  INQUIRY_STEP_FIELDS,
  SUIT_TYPE_OPTIONS,
  UPLOAD_ACCEPT,
  UPLOAD_MAX_COUNT,
  createInquiry,
  describeErrorCode,
  draftEquals,
  emptyInquiryDraft,
  fetchInquiry,
  fetchUpload,
  hasFieldErrors,
  inquiryToDraft,
  runUploadFlow,
  submitInquiry,
  updateInquiry,
  useDocumentTitle,
  validateDesignFile,
  validateInquiryDraft,
  validateInquiryStep,
  type Inquiry,
  type InquiryFieldErrors,
  type InquiryInput,
  type SuitType,
  type UploadScanStatus,
} from "@/modules/commissions/api/commissions";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { PointsIcon, StarLoader } from "@/shared/ui/BrandMark";
import { formatMoneyExact } from "@/shared/lib/format";
import { ApiError, newIdempotencyKey } from "@/shared/lib/http";
import { useToast } from "@/shared/lib/toast";

const route = useRoute();
const router = useRouter();
const site = useSiteStore();
const auth = useAuthStore();
const toast = useToast();
const setTitle = useDocumentTitle();

const DRAFT_STORAGE_KEY = "starium:inquiry-draft-id";

/** 步序标签与 11 文档基线一致；提交结果单独成步，不在表单校验范围内。 */
const STEP_LABELS = ["委托范围", "设定图与角色", "制作需求", "联系偏好", "预览与同意"] as const;
const RESULT_STEP = STEP_LABELS.length;

const draft = reactive<InquiryInput>(emptyInquiryDraft());
const step = ref(0);
const errors = ref<InquiryFieldErrors>({});
const stepError = ref<string | null>(null);

const seriesOptions = ref<SeriesDetail[]>([]);
const seriesLoading = ref(true);
const seriesError = ref<string | null>(null);

const inquiryId = ref<string | null>(null);
const inquiryVersion = ref(0);
const serverSnapshot = ref<InquiryInput>(emptyInquiryDraft());
const submitted = ref<Inquiry | null>(null);

/**
 * C28「服务先申请再报价」：从已通过审批的服务申请进入本页时带上申请编号，
 * 保存在草稿与建单请求里。服务端会再校验归属、类型、审批状态与购买窗口，
 * 前端只负责把它带过去，不做任何本地判定。
 */
const applicationId = ref<string>(
  typeof route.query.application_id === "string" && /^[A-Za-z0-9_-]{1,64}$/.test(route.query.application_id)
    ? route.query.application_id
    : "",
);

type SaveState = "idle" | "dirty" | "saving" | "saved" | "error";
const saveState = ref<SaveState>("idle");
const saveError = ref<string | null>(null);
const sessionExpired = ref(false);

const restoring = ref(false);
const restoreError = ref<string | null>(null);

const submitting = ref(false);
const submitError = ref<string | null>(null);
const submitErrorCode = ref("");
const submitKey = ref<string | null>(null);

interface DesignAsset {
  id: string;
  filename: string;
  size: number;
  state: UploadScanStatus | "uploading";
  rejectReason: string | null;
}
const assets = ref<DesignAsset[]>([]);
const uploading = ref(false);
const uploadError = ref<string | null>(null);
const fileInput = ref<HTMLInputElement | null>(null);

let saveTimer: ReturnType<typeof setTimeout> | null = null;
let pollTimer: ReturnType<typeof setInterval> | null = null;

/* ---------------- 草稿恢复 ---------------- */

async function restoreDraft(): Promise<void> {
  const queryId = typeof route.query.draft === "string" ? route.query.draft : "";
  let stored: string | null = null;
  try {
    stored = localStorage.getItem(DRAFT_STORAGE_KEY);
  } catch {
    // 隐私模式下 localStorage 不可用：只跳过本地草稿 id，不写入任何敏感内容。
    stored = null;
  }
  const candidate = queryId || stored || "";
  if (!candidate) return;
  // 只接受形如服务端 ID 的取值，避免把任意字符串拼进请求路径。
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(candidate)) {
    clearStoredDraftId();
    return;
  }

  restoring.value = true;
  try {
    const inquiry = await fetchInquiry(candidate);
    applyInquiry(inquiry);
  } catch (caught) {
    clearStoredDraftId();
    restoreError.value =
      caught instanceof ApiError
        ? `没有恢复上次的草稿：${describeErrorCode(caught.code, caught.message)}`
        : "没有恢复上次的草稿，可以重新填写。";
  } finally {
    restoring.value = false;
  }
}

function applyInquiry(inquiry: Inquiry): void {
  inquiryId.value = inquiry.id;
  inquiryVersion.value = inquiry.version;
  Object.assign(draft, inquiryToDraft(inquiry));
  serverSnapshot.value = inquiryToDraft(inquiry);
  assets.value = (inquiry.design_asset_ids ?? []).map((id) => ({
    id,
    filename: `已上传设定图 ${id.slice(0, 8)}`,
    size: 0,
    state: "ready" as UploadScanStatus,
    rejectReason: null,
  }));
  saveState.value = "saved";
  if (inquiry.status === "SUBMITTED" || inquiry.status === "QUOTED" || inquiry.status === "ACCEPTED") {
    submitted.value = inquiry;
    step.value = RESULT_STEP;
  }
  storeDraftId(inquiry.id);
}

function storeDraftId(id: string): void {
  try {
    localStorage.setItem(DRAFT_STORAGE_KEY, id);
  } catch {
    // 忽略：草稿本体在服务端，本地存不下不影响恢复链接参数。
  }
}

function clearStoredDraftId(): void {
  inquiryId.value = null;
  inquiryVersion.value = 0;
  try {
    localStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    // 忽略
  }
}

/**
 * 提交成功后只移除本地草稿编号，保留本次询价编号与版本。
 * 页面后续（重复点击、结果区展示）仍需要它们，清空会让“已提交”状态自我矛盾。
 */
function clearLocalDraftMarker(): void {
  try {
    localStorage.removeItem(DRAFT_STORAGE_KEY);
  } catch {
    // 忽略
  }
}

/* ---------------- 系列选项 ---------------- */

async function loadSeries(): Promise<void> {
  seriesLoading.value = true;
  seriesError.value = null;
  try {
    const page = await fetchSeriesList();
    // 只提供已发布系列；下架系列不下发到表单，避免提交时被拒。
    seriesOptions.value = page.items.filter((item) => item.spec?.is_active !== false);
  } catch (caught) {
    seriesError.value = caught instanceof ApiError ? caught.message : "系列列表加载失败。";
  } finally {
    seriesLoading.value = false;
  }
}

/* ---------------- 自动保存 ---------------- */

const isDirty = computed(() => !draftEquals(draft, serverSnapshot.value));

watch(
  () => ({ ...draft, design_asset_ids: [...draft.design_asset_ids] }),
  () => {
    if (submitted.value) return;
    if (isDirty.value) {
      saveState.value = "dirty";
      scheduleSave();
    }
  },
  { deep: true },
);

function scheduleSave(): void {
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(() => {
    void saveDraft();
  }, 1200);
}

/** 有内容的草稿才需要落库，避免用户刚打开页面就产生空记录。 */
function hasContent(value: InquiryInput): boolean {
  return Boolean(
    value.character_name.trim() ||
      value.species.trim() ||
      value.series_id ||
      value.requirements.trim() ||
      value.delivery_region.trim() ||
      value.design_asset_ids.length > 0 ||
      value.budget_min_cents !== null ||
      value.budget_max_cents !== null ||
      value.target_date,
  );
}

/**
 * 采纳服务端保存结果：只取身份（编号 / 版本）与提交状态，
 * 不用服务端回显整份覆盖本页视图——保存请求在途时用户可能继续输入或继续上传，
 * 整体覆盖会把这段时间的改动和上传列表一起抹掉（更新分支同样是「本地优先」）。
 */
function adoptSavedInquiry(inquiry: Inquiry): void {
  inquiryId.value = inquiry.id;
  inquiryVersion.value = inquiry.version ?? inquiryVersion.value;
  serverSnapshot.value = inquiryToDraft({ ...inquiry, ...draft } as Inquiry);
  storeDraftId(inquiry.id);
  if (inquiry.status === "SUBMITTED" || inquiry.status === "QUOTED" || inquiry.status === "ACCEPTED") {
    submitted.value = inquiry;
    step.value = RESULT_STEP;
  }
}

/**
 * 保存草稿。
 *
 * 注意：这里不判断 `isDirty`。移除设定图、上传完成等操作会改变服务端记录，
 * 但可能不改变本地比较快照；调用方（防抖、显式保存、提交前）已经决定是否需要保存。
 */
async function saveDraft(): Promise<boolean> {
  if (submitted.value) return true;
  if (!hasContent(draft)) return true;
  if (!draft.rights_declaration) {
    // 服务端要求建单时就带权利声明（`_check_rights`），草稿也不例外。
    // 与其发一个注定 422 的请求再显示「提交的内容不符合要求」，不如直接告诉用户下一步动作。
    saveState.value = "idle";
    saveError.value = "勾选「我确认有权提供这份角色设定」后会自动保存草稿；未勾选前内容只保留在本页。";
    return false;
  }
  if (saveTimer) {
    clearTimeout(saveTimer);
    saveTimer = null;
  }
  saveState.value = "saving";
  saveError.value = null;
  try {
    if (!inquiryId.value) {
      const created = await createInquiry({
        ...draft,
        design_asset_ids: [...draft.design_asset_ids],
        application_id: applicationId.value || null,
      });
      adoptSavedInquiry(created);
      saveState.value = "saved";
      return true;
    }
    const payload = { ...draft, design_asset_ids: [...draft.design_asset_ids], version: inquiryVersion.value };
    const updated = await updateInquiry(inquiryId.value, payload);
    inquiryVersion.value = updated.version ?? inquiryVersion.value;
    serverSnapshot.value = inquiryToDraft({ ...updated, ...draft } as Inquiry);
    saveState.value = "saved";
    return true;
  } catch (caught) {
    if (caught instanceof ApiError && caught.needsLogin) {
      sessionExpired.value = true;
      saveState.value = "error";
      saveError.value = "登录状态已过期，草稿内容仍保留在本页，请重新登录后继续。";
      return false;
    }
    if (caught instanceof ApiError && caught.isVersionConflict) {
      // 版本冲突：不覆盖服务端，也不丢弃本地输入。
      saveState.value = "error";
      saveError.value = "草稿已在其他地方被更新。你的本次输入仍保留在页面上，请刷新后合并再保存。";
      return false;
    }
    saveState.value = "error";
    saveError.value =
      caught instanceof ApiError
        ? describeErrorCode(caught.code, caught.message)
        : "草稿保存失败，本次输入仍保留在页面上，可以重试。";
    return false;
  }
}

const saveStateLabel = computed(() => {
  switch (saveState.value) {
    case "saving":
      return "正在保存草稿…";
    case "saved":
      return "草稿已保存到你的账户，刷新后可恢复。";
    case "dirty":
      return "有未保存的修改。";
    case "error":
      return saveError.value ?? "草稿保存失败。";
    default:
      // 权利声明未勾选时给出明确下一步，而不是让用户以为已保存（saveError 里写了原因）。
      return saveError.value ?? "填写内容会自动保存为草稿。";
  }
});

/* ---------------- 上传 ---------------- */

function onPickFiles(): void {
  fileInput.value?.click();
}

async function onFilesChosen(event: Event): Promise<void> {
  const input = event.target as HTMLInputElement;
  const files = Array.from(input.files ?? []);
  input.value = "";
  if (files.length === 0) return;
  uploadError.value = null;

  const slots = UPLOAD_MAX_COUNT - assets.value.length;
  if (slots <= 0) {
    uploadError.value = "最多上传 3 张设定图，请先移除多余的文件。";
    return;
  }
  const accepted = files.slice(0, slots);
  if (files.length > slots) {
    uploadError.value = `最多上传 3 张设定图，本次只处理前 ${slots} 张。`;
  }

  uploading.value = true;
  try {
    for (const file of accepted) {
      const localError = validateDesignFile(file);
      if (localError) {
        uploadError.value = localError;
        continue;
      }
      await uploadOne(file);
    }
  } finally {
    uploading.value = false;
    void saveDraft();
  }
}

async function uploadOne(file: File): Promise<void> {
  const placeholder: DesignAsset = {
    id: `pending-${file.name}-${file.size}`,
    filename: file.name,
    size: file.size,
    state: "uploading",
    rejectReason: null,
  };
  assets.value = [...assets.value, placeholder];
  try {
    // 与反馈附件共用同一套「建会话 → 传内容 → complete → 取扫描状态」流程，
    // 避免各页各写一份不一致的调用顺序（见 commissions API 的 runUploadFlow）。
    const uploaded = await runUploadFlow(file, { purpose: "design" });
    replaceAsset(placeholder.id, {
      id: uploaded.asset_id,
      filename: uploaded.filename,
      size: uploaded.size,
      state: uploaded.scan_status,
      rejectReason: uploaded.reject_reason,
    });
    draft.design_asset_ids = assets.value
      .filter((item) => item.state === "ready")
      .map((item) => item.id);
    if (uploaded.scan_status !== "ready") startScanPolling();
  } catch (caught) {
    removeAsset(placeholder.id);
    uploadError.value =
      caught instanceof ApiError
        ? describeErrorCode(caught.code, caught.message)
        : "上传失败，请检查网络后重试；已上传的文件可以重复使用。";
  }
}

function replaceAsset(placeholderId: string, next: DesignAsset): void {
  assets.value = assets.value.map((item) => (item.id === placeholderId ? next : item));
}

function removeAsset(id: string): void {
  assets.value = assets.value.filter((item) => item.id !== id);
  draft.design_asset_ids = assets.value
    .filter((item) => item.state === "ready")
    .map((item) => item.id);
}

function removeAssetAndSave(id: string): void {
  removeAsset(id);
  void saveDraft();
}

/** 上传成功但扫描未完成：轮询扫描结果，ready 之前不允许提交。 */
function startScanPolling(): void {
  if (pollTimer) return;
  pollTimer = setInterval(() => {
    void refreshScanStates();
  }, 4000);
}

async function refreshScanStates(): Promise<void> {
  const pending = assets.value.filter(
    (item) => item.state === "pending" || item.state === "scanning",
  );
  if (pending.length === 0) {
    stopScanPolling();
    return;
  }
  for (const item of pending) {
    try {
      const status = await fetchUpload(item.id);
      replaceAsset(item.id, { ...item, state: status.scan_status, rejectReason: status.reject_reason ?? null });
    } catch {
      // 单次查询失败不改变状态，下一轮继续；不把网络问题当成扫描失败。
    }
  }
  draft.design_asset_ids = assets.value.filter((item) => item.state === "ready").map((item) => item.id);
}

function stopScanPolling(): void {
  if (pollTimer) {
    clearInterval(pollTimer);
    pollTimer = null;
  }
}

const uploadsReady = computed(
  () => assets.value.length > 0 && assets.value.every((item) => item.state === "ready"),
);

const uploadStateHint = computed(() => {
  if (assets.value.length === 0) return "至少 1 张，最多 3 张；单张不超过 10MiB。";
  if (assets.value.some((item) => item.state === "uploading")) return "正在上传，请勿关闭页面。";
  if (assets.value.some((item) => item.state === "rejected")) return "有文件未通过安全检查，需要更换后重新上传。";
  if (!uploadsReady.value) return "还有文件在安全扫描中，通过后才能提交询价。";
  return "全部设定图已通过安全检查，可以继续。";
});

/* ---------------- 校验与步骤 ---------------- */

const currentFields = computed(() => INQUIRY_STEP_FIELDS[step.value] ?? []);

function fieldError(field: keyof InquiryInput): string {
  if (!currentFields.value.includes(field)) return "";
  return errors.value[field] ?? "";
}

function validateCurrentStep(): boolean {
  const result = validateInquiryStep(step.value, draft, { uploadsReady: uploadsReady.value });
  errors.value = result;
  stepError.value = hasFieldErrors(result) ? "请先修正本步中标出的问题，再进入下一步。" : null;
  return !hasFieldErrors(result);
}

function goNext(): void {
  if (!validateCurrentStep()) return;
  step.value = Math.min(step.value + 1, STEP_LABELS.length - 1);
  stepError.value = null;
}

function goPrev(): void {
  step.value = Math.max(step.value - 1, 0);
  errors.value = {};
  stepError.value = null;
}

/**
 * 步骤条导航：允许直接查看任意一步（含未填写的前置步骤）。
 *
 * 之前的实现只允许回退，点击前面的步骤按钮毫无反应——步骤条渲染的是可用按钮，
 * 点了没反应属于「控件不生效」。进入下一步仍由 `goNext` 校验，提交前 `onSubmit`
 * 还会对全部步骤做一次校验并跳回第一个有问题的步骤，因此直接跳步不会绕过校验。
 */
function goTo(target: number): void {
  if (!Number.isFinite(target)) return;
  step.value = Math.min(Math.max(Math.floor(target), 0), STEP_LABELS.length - 1);
  errors.value = {};
  stepError.value = null;
}

/* ---------------- 金额输入 ---------------- */

const budgetMinYuan = ref<string>("");
const budgetMaxYuan = ref<string>("");

/** 元 → 分：只接受数字与小数，最多两位；空值表示未填写。 */
function parseYuanToCents(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (!/^\d+(\.\d{1,2})?$/.test(trimmed)) return Number.NaN;
  return Math.round(Number(trimmed) * 100);
}

function onBudgetInput(which: "min" | "max"): void {
  const parsed = parseYuanToCents(which === "min" ? budgetMinYuan.value : budgetMaxYuan.value);
  if (Number.isNaN(parsed)) {
    errors.value = {
      ...errors.value,
      [which === "min" ? "budget_min_cents" : "budget_max_cents"]: "金额最多两位小数，例如 8000 或 8000.50。",
    };
    return;
  }
  if (which === "min") draft.budget_min_cents = parsed;
  else draft.budget_max_cents = parsed;
  const next = { ...errors.value };
  delete next.budget_min_cents;
  delete next.budget_max_cents;
  errors.value = next;
}

const seriesTitle = computed(
  () => seriesOptions.value.find((item) => item.id === draft.series_id)?.title ?? "未选择",
);

const suitTypeLabel = computed(
  () => SUIT_TYPE_OPTIONS.find((item) => item.value === draft.suit_type)?.label ?? "未选择",
);

const contactLabel = computed(() => {
  if (!draft.contact_preference) return "未指定（默认使用账号手机号）";
  return (
    CONTACT_PREFERENCE_OPTIONS.find((item) => item.value === draft.contact_preference)?.label ??
    draft.contact_preference
  );
});

/* ---------------- 提交 ---------------- */

async function onSubmit(): Promise<void> {
  if (submitting.value || submitted.value) return;
  submitError.value = null;
  submitErrorCode.value = "";

  // 提交前把每一步都校验一遍，不只校验当前步。
  const all = validateInquiryDraft(draft, { uploadsReady: uploadsReady.value });
  if (hasFieldErrors(all)) {
    errors.value = all;
    // 跳到第一个有问题的步骤，避免用户在当前步找不到报错。
    const firstInvalidStep = INQUIRY_STEP_FIELDS.findIndex((fields) =>
      fields.some((field) => all[field]),
    );
    step.value = firstInvalidStep >= 0 ? firstInvalidStep : STEP_LABELS.length - 1;
    stepError.value = "还有必填内容没有完成，请按提示逐项修正。";
    return;
  }

  submitting.value = true;
  try {
    const saved = await saveDraft();
    if (!saved || !inquiryId.value) {
      submitError.value = saveError.value ?? "草稿保存失败，无法提交。本次输入仍保留在页面上。";
      return;
    }
    // 幂等键在整个提交流程内固定：重复点击或网络重试都只产生一条提交记录。
    if (!submitKey.value) submitKey.value = newIdempotencyKey();
    const result = await submitInquiry(inquiryId.value, inquiryVersion.value, submitKey.value);
    const confirmed = await fetchInquiry(result.inquiry_id);
    submitted.value = confirmed;
    inquiryVersion.value = confirmed.version ?? result.version;
    step.value = RESULT_STEP;
    clearLocalDraftMarker();
    toast.success("询价已提交", "工作人员会按人工响应时间处理，你可以随时在账户内查看状态。");
  } catch (caught) {
    if (caught instanceof ApiError) {
      submitErrorCode.value = caught.code;
      if (caught.needsLogin) {
        sessionExpired.value = true;
        submitError.value = "登录状态已过期。内容仍保留在本页，请重新登录后继续提交。";
        return;
      }
      if (caught.isVersionConflict) {
        submitError.value = "询价内容已在其他地方更新，请刷新后核对最新版本再提交。";
        return;
      }
      submitError.value = describeErrorCode(caught.code, caught.message);
      if (Object.keys(caught.fields ?? {}).length > 0) {
        errors.value = { ...errors.value, ...(caught.fields as InquiryFieldErrors) };
      }
      return;
    }
    submitError.value = "提交失败，请检查网络后重试。已填写的内容不会丢失。";
  } finally {
    submitting.value = false;
  }
}

const submitDisabledReason = computed(() => {
  if (submitted.value) return "这份询价已经提交，不能重复提交。";
  if (!uploadsReady.value) return "设定图还未通过安全检查。";
  return "";
});

/* ---------------- 生命周期 ---------------- */

onMounted(async () => {
  setTitle("提交询价");
  void site.load();
  if (!auth.loaded) {
    await auth.loadMe().catch(() => null);
  }
  await Promise.all([loadSeries(), restoreDraft()]);
});

onBeforeUnmount(() => {
  stopScanPolling();
  if (saveTimer) clearTimeout(saveTimer);
});

async function onManualSave(): Promise<void> {
  const ok = await saveDraft();
  if (ok) toast.success("草稿已保存");
}

function retrySubmit(): void {
  submitError.value = null;
  void onSubmit();
}

const userCurrency = computed(() => auth.currency);

async function goToQuotes(): Promise<void> {
  await router.push("/account/quotes");
}

/**
 * 供 Vitest 直接驱动的测试句柄。
 * 只暴露表单状态与校验/提交入口，不改变任何生产逻辑。
 */
defineExpose({
  draft,
  step,
  errors,
  stepError,
  assets,
  uploading,
  uploadError,
  seriesOptions,
  inquiryId,
  inquiryVersion,
  submitted,
  submitting,
  submitError,
  submitErrorCode,
  saveState,
  saveError,
  sessionExpired,
  applyInquiry,
  validateCurrentStep,
  validateAll: () => validateInquiryDraft(draft, { uploadsReady: uploadsReady.value }),
  goNext,
  goPrev,
  goTo,
  onBudgetInput,
  budgetMinYuan,
  budgetMaxYuan,
  onFilesChosen,
  removeAssetAndSave,
  saveDraft,
  onSubmit,
  uploadOne,
  submitDisabledReason,
  uploadsReady,
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs
      :items="[
        { label: '首页', to: '/' },
        { label: '委托', to: '/commission/guide' },
        { label: '提交询价' },
      ]"
    />

    <header class="mt-6 max-w-3xl">
      <p class="eyebrow">Inquiry</p>
      <h1 class="mt-3 font-display text-3xl leading-tight sm:text-4xl">提交询价</h1>
      <p class="mt-4 text-sm leading-relaxed text-ink-muted sm:text-base">
        询价是需求沟通记录，不是订单。量体信息会在接单后单独收集，
        这里不需要填写生日、性别或住址。
      </p>
    </header>

    <!-- 会话过期提示：保留输入，引导重新登录 -->
    <div v-if="sessionExpired" class="mt-6 rounded-lg border border-wine bg-wine-soft p-5 text-sm text-wine" role="alert">
      <p class="font-medium">登录状态已过期</p>
      <p class="mt-2">
        你填写的内容仍保留在当前页面，草稿也在服务器上。重新登录后回到本页即可继续提交。
      </p>
      <div class="mt-4">
        <AppButton
          size="sm"
          variant="secondary"
          :to="`/login?returnTo=${encodeURIComponent(route.fullPath)}`"
        >
          重新登录
        </AppButton>
      </div>
    </div>

    <div v-if="restoreError" class="mt-6 rounded-lg border border-amber bg-amber-soft p-4 text-sm text-amber-strong" role="status">
      {{ restoreError }}
    </div>

    <!-- 提交成功 -->
    <template v-if="submitted">
      <SurfaceCard class="mt-8" data-testid="submitted">
        <div class="flex flex-wrap items-center gap-3">
          <StatusBadge tone="jade" label="已提交" />
          <StatusBadge tone="neutral" :label="`询价编号 ${submitted.id}`" />
        </div>
        <h2 class="mt-4 font-display text-2xl">询价已提交，进入人工处理</h2>
        <p class="mt-3 text-sm leading-relaxed text-ink-muted">
          提交时间 {{ submitted.submitted_at ?? "—" }}。人工响应时间为
          {{ site.brand.response_sla.inquiry_workdays }} 个工作日；需要补件时状态会变成“待补件”，
          并在账户内说明需要补充什么。
        </p>
        <dl class="mt-6 grid gap-4 text-sm sm:grid-cols-2">
          <div>
            <dt class="text-ink-faint">角色</dt>
            <dd class="mt-1 text-ink-soft">{{ draft.character_name }} · {{ draft.species }}</dd>
          </div>
          <div>
            <dt class="text-ink-faint">系列与类型</dt>
            <dd class="mt-1 text-ink-soft">{{ seriesTitle }} · {{ suitTypeLabel }}</dd>
          </div>
          <div>
            <dt class="text-ink-faint">设定图</dt>
            <dd class="mt-1 text-ink-soft">{{ draft.design_asset_ids.length }} 张（已通过安全检查）</dd>
          </div>
          <div>
            <dt class="text-ink-faint">联系偏好</dt>
            <dd class="mt-1 text-ink-soft">{{ contactLabel }}</dd>
          </div>
        </dl>
        <p class="mt-6 rounded-sm bg-canvas-sunk px-4 py-3 text-xs text-ink-muted">
          这份询价已经提交，不能重复提交。需要修改或撤回请在询价详情页操作；
          已提交的内容不会被后来编辑覆盖工作人员已看到的版本。
        </p>
        <div class="mt-6 flex flex-wrap gap-3">
          <AppButton to="/account/quotes">查看我的询价与报价</AppButton>
          <AppButton variant="secondary" @click="goToQuotes()">刷新列表</AppButton>
          <AppButton to="/commission/guide" variant="ghost">阅读委托指南</AppButton>
        </div>
      </SurfaceCard>
    </template>

    <!-- 表单 -->
    <template v-else>
      <!-- 步骤指示 -->
      <nav class="mt-8" aria-label="询价步骤">
        <ol class="flex flex-wrap gap-2 text-xs">
          <li v-for="(label, index) in STEP_LABELS" :key="label">
            <button
              type="button"
              class="flex min-h-[44px] items-center gap-2 rounded-sm border px-3 text-left transition-colors"
              :class="
                index === step
                  ? 'border-amber bg-amber-soft text-amber-strong font-medium'
                  : index < step
                    ? 'border-line bg-surface text-ink-soft'
                    : 'border-line bg-surface-muted text-ink-faint'
              "
              :data-testid="`step-${index}`"
              :aria-current="index === step ? 'step' : undefined"
              @click="goTo(index)"
            >
              <span aria-hidden="true">{{ index + 1 }}</span>
              <span>{{ label }}</span>
            </button>
          </li>
        </ol>
      </nav>

      <div class="mt-6 grid gap-6 lg:grid-cols-[1.35fr_0.65fr]">
        <div>
          <!-- 步骤 1：委托范围 -->
          <SurfaceCard v-if="step === 0">
            <h2 class="font-display text-xl">第 1 步 · 委托范围</h2>
            <p class="mt-2 text-sm text-ink-muted">
              系列决定报价范围；类型决定结构复杂度。两者都可以在沟通中调整，但需要工作人员重新评估。
            </p>

            <div v-if="seriesLoading" class="mt-6" role="status" aria-live="polite">
              <StarLoader :size="64" message="正在读取系列" />
            </div>
            <EmptyState
              v-else-if="seriesError"
              class="mt-6"
              tone="error"
              title="系列列表加载失败"
              :description="`${seriesError} 没有系列可选时无法提交询价，请重试。`"
            >
              <AppButton size="sm" @click="loadSeries()">重新加载</AppButton>
            </EmptyState>
            <EmptyState
              v-else-if="seriesOptions.length === 0"
              class="mt-6"
              title="暂无可询价系列"
              description="系列参数由运营维护并发布；没有已发布系列时无法确定报价范围。可以先联系客服登记需求。"
            >
              <AppButton size="sm" variant="secondary" to="/contact">联系客服</AppButton>
            </EmptyState>

            <div v-else class="mt-6 space-y-6">
              <FieldRow label="角色名称" for="character_name" required :error="fieldError('character_name')" hint="1–60 字，用于识别这只角色。">
                <input
                  id="character_name"
                  v-model.trim="draft.character_name"
                  class="field-base"
                  type="text"
                  maxlength="60"
                  autocomplete="off"
                  :aria-invalid="Boolean(fieldError('character_name'))"
                />
              </FieldRow>

              <FieldRow label="物种" for="species" required :error="fieldError('species')" hint="例如“龙”“狼”“猫科”，1–60 字。">
                <input
                  id="species"
                  v-model.trim="draft.species"
                  class="field-base"
                  type="text"
                  maxlength="60"
                  autocomplete="off"
                  :aria-invalid="Boolean(fieldError('species'))"
                />
              </FieldRow>

              <FieldRow label="系列" for="series_id" required :error="fieldError('series_id')" hint="只列出已发布系列；下架系列不会出现在这里。">
                <select id="series_id" v-model="draft.series_id" class="field-base">
                  <option value="">请选择系列</option>
                  <option v-for="item in seriesOptions" :key="item.id" :value="item.id">
                    {{ item.title }}{{ item.spec ? ` · ${formatMoneyExact(item.spec.base_price_cents, item.spec.currency)} 起` : "" }}
                  </option>
                </select>
              </FieldRow>

              <fieldset>
                <legend class="text-sm font-medium text-ink-soft">
                  制作类型 <span class="text-xs text-wine">必填</span>
                </legend>
                <div class="mt-3 grid gap-3 sm:grid-cols-2">
                  <label
                    v-for="item in SUIT_TYPE_OPTIONS"
                    :key="item.value"
                    class="flex cursor-pointer items-start gap-3 rounded-sm border p-4 transition-colors"
                    :class="draft.suit_type === item.value ? 'border-amber bg-amber-soft' : 'border-line bg-surface hover:border-line-strong'"
                  >
                    <input
                      v-model="draft.suit_type"
                      class="mt-1"
                      type="radio"
                      name="suit_type"
                      :value="item.value as SuitType"
                    />
                    <span>
                      <span class="block text-sm font-medium">{{ item.label }}</span>
                      <span class="mt-1 block text-xs text-ink-muted">{{ item.hint }}</span>
                    </span>
                  </label>
                </div>
                <p v-if="fieldError('suit_type')" class="mt-2 text-xs font-medium text-wine" role="alert">
                  {{ fieldError("suit_type") }}
                </p>
              </fieldset>
            </div>
          </SurfaceCard>

          <!-- 步骤 2：设定图与角色 -->
          <SurfaceCard v-else-if="step === 1">
            <h2 class="font-display text-xl">第 2 步 · 设定图与角色</h2>
            <p class="mt-2 text-sm text-ink-muted">
              设定图 1–3 张、单张不超过 10MiB，仅支持 JPEG / PNG / WebP。文件按私密附件保存，
              只有你和被分配的工作人员可以访问。
            </p>

            <div class="mt-6">
              <input
                ref="fileInput"
                class="sr-only"
                type="file"
                :accept="UPLOAD_ACCEPT"
                multiple
                @change="onFilesChosen"
              />
              <AppButton
                variant="secondary"
                :loading="uploading"
                :disabled="assets.length >= UPLOAD_MAX_COUNT"
                :disabled-reason="assets.length >= UPLOAD_MAX_COUNT ? '已经上传 3 张，需要先移除一张。' : ''"
                @click="onPickFiles"
              >
                选择设定图
              </AppButton>
              <p class="mt-3 text-xs text-ink-faint">{{ uploadStateHint }}</p>
              <p v-if="uploadError" class="mt-2 text-xs font-medium text-wine" role="alert">{{ uploadError }}</p>
              <p v-if="fieldError('design_asset_ids')" class="mt-2 text-xs font-medium text-wine" role="alert">
                {{ fieldError("design_asset_ids") }}
              </p>
            </div>

            <ul v-if="assets.length" class="mt-6 divide-y divide-line border-y border-line">
              <li v-for="asset in assets" :key="asset.id" class="flex flex-wrap items-center gap-3 py-3">
                <StatusBadge
                  :tone="asset.state === 'ready' ? 'jade' : asset.state === 'rejected' ? 'wine' : 'amber'"
                  :label="
                    asset.state === 'uploading'
                      ? '正在上传'
                      : asset.state === 'ready'
                        ? '可提交'
                        : asset.state === 'rejected'
                          ? '未通过检查'
                          : '安全扫描中'
                  "
                />
                <span class="min-w-0 flex-1 truncate text-sm text-ink-soft">{{ asset.filename }}</span>
                <span v-if="asset.size" class="text-xs tabular-nums text-ink-faint">
                  {{ (asset.size / (1024 * 1024)).toFixed(1) }} MiB
                </span>
                <AppButton size="sm" variant="quiet" @click="removeAssetAndSave(asset.id)">移除</AppButton>
              </li>
            </ul>

            <p v-if="assets.some((item) => item.rejectReason)" class="mt-3 text-xs text-wine">
              {{ assets.find((item) => item.rejectReason)?.rejectReason }}
            </p>

            <p class="mt-6 rounded-sm bg-canvas-sunk px-4 py-3 text-xs leading-relaxed text-ink-muted">
              上传成功后系统会做安全检查，状态为“可提交”时才允许提交询价。
              如果询价提交失败，已上传的附件仍属于你的账号，可以继续复用。
            </p>
          </SurfaceCard>

          <!-- 步骤 3：制作需求 -->
          <SurfaceCard v-else-if="step === 2">
            <h2 class="font-display text-xl">第 3 步 · 制作需求</h2>
            <p class="mt-2 text-sm text-ink-muted">
              写清花纹、结构、配件与其他要求。预算与意向日期只用于沟通，不自动定价，
              也不构成交付承诺。
            </p>

            <div class="mt-6 space-y-6">
              <FieldRow
                label="需求说明"
                for="requirements"
                :error="fieldError('requirements')"
                hint="0–3000 字。可拆卸结构、散热、视野、配件等特殊要求请写在这里。"
              >
                <textarea
                  id="requirements"
                  v-model="draft.requirements"
                  class="field-base min-h-[160px]"
                  maxlength="3000"
                  :aria-invalid="Boolean(fieldError('requirements'))"
                />
                <p class="mt-1 text-right text-xs tabular-nums text-ink-faint">
                  {{ draft.requirements.length }} / 3000
                </p>
              </FieldRow>

              <div class="grid gap-6 sm:grid-cols-2">
                <FieldRow
                  label="预算下限（元）"
                  for="budget_min"
                  :error="fieldError('budget_min_cents')"
                  hint="可选；最多两位小数。留空表示还没有明确区间。"
                >
                  <input
                    id="budget_min"
                    v-model="budgetMinYuan"
                    class="field-base tabular-nums"
                    type="text"
                    inputmode="decimal"
                    autocomplete="off"
                    @change="onBudgetInput('min')"
                  />
                </FieldRow>
                <FieldRow
                  label="预算上限（元）"
                  for="budget_max"
                  :error="fieldError('budget_max_cents')"
                  hint="可选；上限不能低于下限。"
                >
                  <input
                    id="budget_max"
                    v-model="budgetMaxYuan"
                    class="field-base tabular-nums"
                    type="text"
                    inputmode="decimal"
                    autocomplete="off"
                    @change="onBudgetInput('max')"
                  />
                </FieldRow>
              </div>

              <p v-if="draft.budget_min_cents !== null || draft.budget_max_cents !== null" class="text-xs text-ink-faint">
                当前区间：
                {{ draft.budget_min_cents === null ? "未填" : formatMoneyExact(draft.budget_min_cents, userCurrency) }}
                –
                {{ draft.budget_max_cents === null ? "未填" : formatMoneyExact(draft.budget_max_cents, userCurrency) }}
                （预算仅辅助沟通，不自动定价）
              </p>

              <div class="grid gap-6 sm:grid-cols-2">
                <FieldRow label="意向日期" for="target_date" hint="可选。意向日期不等于交付承诺。">
                  <input id="target_date" v-model="draft.target_date" class="field-base" type="date" />
                </FieldRow>
                <FieldRow
                  label="收货区域"
                  for="delivery_region"
                  required
                  :error="fieldError('delivery_region')"
                  hint="只填国家或省级范围，用于评估运输可行性；完整地址留到下单时再填。"
                >
                  <input
                    id="delivery_region"
                    v-model.trim="draft.delivery_region"
                    class="field-base"
                    type="text"
                    maxlength="60"
                    autocomplete="off"
                    placeholder="例如：中国大陆 / 上海 / 境外（国家）"
                    :aria-invalid="Boolean(fieldError('delivery_region'))"
                  />
                </FieldRow>
              </div>
            </div>
          </SurfaceCard>

          <!-- 步骤 4：联系偏好 -->
          <SurfaceCard v-else-if="step === 3">
            <h2 class="font-display text-xl">第 4 步 · 联系偏好</h2>
            <p class="mt-2 text-sm text-ink-muted">
              手机号来自已验证账号，用于必要的进度通知。其他联系渠道不代表已经验证身份，
              只作为沟通偏好记录。
            </p>

            <fieldset class="mt-6">
              <legend class="text-sm font-medium text-ink-soft">联系方式偏好（可选）</legend>
              <div class="mt-3 flex flex-wrap gap-3">
                <label
                  v-for="item in CONTACT_PREFERENCE_OPTIONS"
                  :key="item.value"
                  class="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border px-4 text-sm transition-colors"
                  :class="draft.contact_preference === item.value ? 'border-amber bg-amber-soft' : 'border-line bg-surface hover:border-line-strong'"
                >
                  <input v-model="draft.contact_preference" type="radio" name="contact_preference" :value="item.value" />
                  <span>{{ item.label }}</span>
                </label>
                <label
                  class="flex min-h-[44px] cursor-pointer items-center gap-2 rounded-sm border px-4 text-sm transition-colors"
                  :class="draft.contact_preference === null ? 'border-amber bg-amber-soft' : 'border-line bg-surface hover:border-line-strong'"
                >
                  <input v-model="draft.contact_preference" type="radio" name="contact_preference" :value="null" />
                  <span>不指定</span>
                </label>
              </div>
            </fieldset>

            <p class="mt-6 rounded-sm bg-canvas-sunk px-4 py-3 text-xs leading-relaxed text-ink-muted">
              除了手机号，其他联系方式不会用于身份核验。任何要求你提供验证码、支付密码或转账到个人账户的
              消息都不是工作室的行为，请通过 {{ site.brand.support_email }} 核实。
            </p>
          </SurfaceCard>

          <!-- 步骤 5：预览与同意 -->
          <SurfaceCard v-else>
            <h2 class="font-display text-xl">第 5 步 · 预览与同意</h2>
            <p class="mt-2 text-sm text-ink-muted">
              提交前请核对下面的内容。提交后工作人员看到的是这个版本，之后修改会产生新的版本记录。
            </p>

            <dl class="mt-6 divide-y divide-line border-y border-line text-sm">
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">角色 / 物种</dt>
                <dd class="text-ink-soft">{{ draft.character_name || "未填写" }} · {{ draft.species || "未填写" }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">系列 / 类型</dt>
                <dd class="text-ink-soft">{{ seriesTitle }} · {{ suitTypeLabel }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">设定图</dt>
                <dd class="text-ink-soft">
                  {{ assets.filter((item) => item.state === "ready").length }} 张可用
                  <span v-if="assets.length !== assets.filter((item) => item.state === 'ready').length" class="text-wine">
                    （有文件未通过检查）
                  </span>
                </dd>
              </div>
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">预算区间</dt>
                <dd class="text-ink-soft tabular-nums">
                  {{ draft.budget_min_cents === null ? "未填" : formatMoneyExact(draft.budget_min_cents, userCurrency) }}
                  –
                  {{ draft.budget_max_cents === null ? "未填" : formatMoneyExact(draft.budget_max_cents, userCurrency) }}
                </dd>
              </div>
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">意向日期</dt>
                <dd class="text-ink-soft tabular-nums">{{ draft.target_date || "未填" }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">收货区域</dt>
                <dd class="text-ink-soft">{{ draft.delivery_region || "未填写" }}</dd>
              </div>
              <div class="flex flex-wrap justify-between gap-2 py-3">
                <dt class="text-ink-faint">联系偏好</dt>
                <dd class="text-ink-soft">{{ contactLabel }}</dd>
              </div>
              <div v-if="draft.requirements" class="py-3">
                <dt class="text-ink-faint">需求说明</dt>
                <dd class="mt-2 whitespace-pre-wrap text-ink-soft">{{ draft.requirements }}</dd>
              </div>
            </dl>

            <div class="mt-6 space-y-4">
              <label class="flex items-start gap-3 text-sm">
                <input v-model="draft.rights_declaration" class="mt-1" type="checkbox" />
                <span>
                  <span class="font-medium text-ink-soft">我确认有权提供这份角色设定</span>
                  <span class="mt-1 block text-xs text-ink-muted">
                    我拥有该设定，或已获得设定权利人的使用授权；如有第三方主张权利，我会配合说明来源。
                    本项必须勾选才能提交。
                  </span>
                </span>
              </label>
              <p v-if="fieldError('rights_declaration')" class="text-xs font-medium text-wine" role="alert">
                {{ fieldError("rights_declaration") }}
              </p>

              <label class="flex items-start gap-3 text-sm">
                <input v-model="draft.guardian_required" class="mt-1" type="checkbox" />
                <span>
                  <span class="font-medium text-ink-soft">未成年委托人（需监护人同意）</span>
                  <span class="mt-1 block text-xs text-ink-muted">
                    勾选后工作人员会先说明监护人同意的要求，再继续后续流程。
                    这里不收集监护人身份材料，也不用一个勾选框代替完整的同意流程。
                  </span>
                </span>
              </label>
            </div>

            <div v-if="submitError" class="mt-6 rounded-lg border border-wine bg-wine-soft p-4 text-sm text-wine" role="alert">
              <p class="font-medium">提交未完成</p>
              <p class="mt-2">{{ submitError }}</p>
              <p v-if="submitErrorCode" class="mt-2 text-xs">错误码：{{ submitErrorCode }}</p>
              <div class="mt-4 flex flex-wrap gap-3">
                <AppButton size="sm" variant="secondary" :loading="submitting" @click="retrySubmit">重试提交</AppButton>
                <AppButton size="sm" variant="ghost" @click="goTo(0)">返回检查内容</AppButton>
              </div>
            </div>
          </SurfaceCard>

          <!-- 步骤级提示与操作 -->
          <div v-if="stepError" class="mt-4 rounded-lg border border-wine bg-wine-soft p-4 text-sm text-wine" role="alert">
            {{ stepError }}
          </div>

          <div class="mt-6 flex flex-wrap items-center gap-3">
            <AppButton v-if="step > 0" variant="secondary" @click="goPrev">上一步</AppButton>
            <span v-if="step < STEP_LABELS.length - 1" data-testid="next">
              <AppButton @click="goNext">下一步</AppButton>
            </span>
            <span v-else data-testid="submit">
              <AppButton
                :loading="submitting"
                :disabled="Boolean(submitDisabledReason)"
                :disabled-reason="submitDisabledReason"
                @click="onSubmit"
              >
                提交询价
              </AppButton>
            </span>
            <AppButton variant="quiet" :loading="saveState === 'saving'" @click="onManualSave">保存草稿</AppButton>
          </div>
          <p
            class="mt-3 text-xs"
            :class="saveState === 'error' ? 'text-wine' : 'text-ink-faint'"
            aria-live="polite"
            data-testid="save-state"
          >
            {{ saveStateLabel }}
          </p>
        </div>

        <!-- 侧栏：状态与规则 -->
        <aside class="space-y-6 lg:sticky lg:top-24 lg:self-start">
          <SurfaceCard>
            <h2 class="font-display text-base">提交状态</h2>
            <ul class="mt-3 space-y-2 text-xs text-ink-muted">
              <li>草稿：{{ inquiryId ? `已建立（编号 ${inquiryId}）` : "尚未建立，填写后自动创建" }}</li>
              <li>版本：{{ inquiryVersion || 0 }}</li>
              <li>设定图：{{ assets.filter((item) => item.state === "ready").length }} / 3 已可用</li>
              <li>账号：{{ auth.displayName }}</li>
            </ul>
            <p class="mt-3 text-xs text-ink-faint">
              草稿保存在你的账户内，本地只记录草稿编号；敏感内容不写入浏览器存储。
            </p>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">提交之后</h2>
            <ol class="mt-3 space-y-2 text-xs text-ink-muted">
              <li>1. 人工查看并按需补件（状态：待补件）。</li>
              <li>2. 发布报价，报价带 {{ site.commissionPolicy.quote_valid_days }} 天有效期。</li>
              <li>3. 你在有效期内接受报价，才生成订单与首期款项。</li>
            </ol>
            <div class="mt-4 flex items-center gap-2 text-xs text-ink-faint">
              <PointsIcon :size="18" />
              <span>报价与金额都以服务端发布版本为准。</span>
            </div>
          </SurfaceCard>

          <SurfaceCard>
            <h2 class="font-display text-base">隐私提示</h2>
            <p class="mt-3 text-xs leading-relaxed text-ink-muted">
              首次询价不收集生日、性别与住址；量体数据在接单后按需收集，并按高敏感度内部资料保护。
              你可以随时撤回未接受的询价。
            </p>
          </SurfaceCard>
        </aside>
      </div>
    </template>
  </div>
</template>
