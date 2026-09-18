<script setup lang="ts">
/**
 * 收货地址（`/account/addresses`）。
 *
 * 依据 03/11 文档：地址增改删、默认地址；最多 20 条；
 * 删除时明确提示「订单内已保存的地址快照不受影响」。
 * 表单状态：dirty / saving / saved / validation-error / session-expired；
 * `PATCH` 必须带 `version`，409 `VERSION_CONFLICT` 提示刷新后重试。
 */
import { computed, onMounted, ref } from "vue";

import {
  createAddress,
  deleteAddress,
  describeIdentityError,
  errorCodeOf,
  fetchAddresses,
  isSessionExpired,
  phoneFormatError,
  updateAddress,
  type Address,
} from "@/modules/identity/api/identity";
import { useSiteStore } from "@/modules/content/stores/site";
import { AppButton, Breadcrumbs, EmptyState, FieldRow, SectionHeading, SkeletonBlock, StatusBadge, SurfaceCard } from "@/shared/ui/base";
import { useToast } from "@/shared/lib/toast";

const site = useSiteStore();
const toast = useToast();

/** 与后端 `MAX_ADDRESSES` 一致；服务端仍是权威。 */
const MAX_ADDRESSES = 20;

const crumbs = [
  { label: "首页", to: "/" },
  { label: "我的账户", to: "/account" },
  { label: "收货地址" },
];

const addresses = ref<Address[]>([]);
const loading = ref(true);
const loadError = ref("");
const forbidden = ref(false);
const sessionExpired = ref(false);
const savedNotice = ref("");
const deletingId = ref("");
const busyId = ref("");

interface AddressForm {
  id: string | null;
  version: number;
  label: string;
  recipient: string;
  phone: string;
  province: string;
  city: string;
  detail: string;
  postal_code: string;
  is_default: boolean;
}

function emptyForm(): AddressForm {
  return {
    id: null,
    version: 1,
    label: "默认地址",
    recipient: "",
    phone: "",
    province: "",
    city: "",
    detail: "",
    postal_code: "",
    is_default: false,
  };
}

const form = ref<AddressForm>(emptyForm());
const formOpen = ref(false);
const saving = ref(false);
const formError = ref("");
const fieldErrors = ref<Record<string, string>>({});
const dirty = ref(false);
const savedAt = ref<string | null>(null);

const atLimit = computed(() => addresses.value.length >= MAX_ADDRESSES);
const editing = computed(() => form.value.id !== null);

function markDirty(): void {
  dirty.value = true;
  savedAt.value = null;
}

function validate(): boolean {
  const errors: Record<string, string> = {};
  if (!form.value.recipient.trim()) errors["recipient"] = "请填写收件人姓名。";
  const phoneError = phoneFormatError(form.value.phone);
  if (phoneError) errors["phone"] = phoneError;
  if (form.value.detail.trim().length < 4) errors["detail"] = "详细地址至少 4 个字符，请写到门牌号或代收点。";
  if (form.value.detail.trim().length > 200) errors["detail"] = "详细地址最多 200 个字符。";
  if (form.value.label.trim().length > 30) errors["label"] = "地址标签最多 30 个字符。";
  fieldErrors.value = errors;
  return Object.keys(errors).length === 0;
}

async function load(): Promise<void> {
  loading.value = true;
  loadError.value = "";
  forbidden.value = false;
  sessionExpired.value = false;
  try {
    addresses.value = await fetchAddresses();
  } catch (caught) {
    addresses.value = [];
    loadError.value = describeIdentityError(caught);
    forbidden.value = errorCodeOf(caught) === "FORBIDDEN";
    sessionExpired.value = isSessionExpired(caught);
  } finally {
    loading.value = false;
  }
}

function openCreate(): void {
  if (atLimit.value) {
    toast.info("已达地址上限", `最多保存 ${MAX_ADDRESSES} 条地址，请先删除不再使用的地址。`);
    return;
  }
  form.value = emptyForm();
  form.value.is_default = addresses.value.length === 0;
  fieldErrors.value = {};
  formError.value = "";
  savedAt.value = null;
  dirty.value = false;
  formOpen.value = true;
}

function openEdit(row: Address): void {
  form.value = {
    id: row.id,
    version: row.version,
    label: row.label,
    recipient: row.recipient,
    // 服务端只返回脱敏手机号：必须重新录入完整号码才能提交。
    phone: "",
    province: row.province,
    city: row.city,
    detail: row.detail,
    postal_code: row.postal_code ?? "",
    is_default: row.is_default,
  };
  fieldErrors.value = {};
  formError.value = "";
  savedAt.value = null;
  dirty.value = false;
  formOpen.value = true;
}

async function submit(): Promise<void> {
  formError.value = "";
  savedAt.value = null;
  if (!validate()) {
    formError.value = "请先修正标红的字段。";
    return;
  }
  saving.value = true;
  try {
    if (form.value.id) {
      await updateAddress(form.value.id, {
        version: form.value.version,
        label: form.value.label.trim() || "默认地址",
        recipient: form.value.recipient.trim(),
        phone: form.value.phone,
        province: form.value.province.trim(),
        city: form.value.city.trim(),
        detail: form.value.detail.trim(),
        postal_code: form.value.postal_code.trim() || null,
        is_default: form.value.is_default,
      });
      savedNotice.value = "地址已更新。";
    } else {
      await createAddress({
        label: form.value.label.trim() || "默认地址",
        recipient: form.value.recipient.trim(),
        phone: form.value.phone,
        province: form.value.province.trim(),
        city: form.value.city.trim(),
        detail: form.value.detail.trim(),
        postal_code: form.value.postal_code.trim() || null,
        is_default: form.value.is_default,
      });
      savedNotice.value = "地址已保存。下单时会使用服务端保存的地址，并写入订单快照。";
    }
    dirty.value = false;
    savedAt.value = new Date().toISOString();
    formOpen.value = false;
    await load();
  } catch (caught) {
    const code = errorCodeOf(caught);
    if (code === "VERSION_CONFLICT") {
      formError.value = "该地址已在其他地方被修改，请刷新后重新编辑，避免覆盖。";
    } else if (isSessionExpired(caught)) {
      sessionExpired.value = true;
      formError.value = "登录状态已过期，请重新登录后再保存。";
    } else {
      formError.value = describeIdentityError(caught);
    }
  } finally {
    saving.value = false;
  }
}

function askDelete(row: Address): void {
  deletingId.value = row.id;
}

async function confirmDelete(row: Address): Promise<void> {
  busyId.value = row.id;
  try {
    await deleteAddress(row.id);
    deletingId.value = "";
    savedNotice.value = "地址已删除。订单内已保存的地址快照不受影响，历史订单仍按当时的收件信息履约与留证。";
    await load();
  } catch (caught) {
    if (errorCodeOf(caught) === "RESOURCE_NOT_FOUND") {
      savedNotice.value = "该地址已不存在，列表已刷新。";
      await load();
    } else {
      toast.error("删除失败", describeIdentityError(caught));
    }
  } finally {
    busyId.value = "";
  }
}

async function setDefault(row: Address): Promise<void> {
  if (row.is_default) return;
  busyId.value = row.id;
  try {
    await updateAddress(row.id, { version: row.version, is_default: true });
    savedNotice.value = "已设为默认地址。";
    await load();
  } catch (caught) {
    if (errorCodeOf(caught) === "VERSION_CONFLICT") {
      savedNotice.value = "该地址刚被其他操作更新，已重新加载列表，请再试一次。";
      await load();
    } else {
      toast.error("设置默认地址失败", describeIdentityError(caught));
    }
  } finally {
    busyId.value = "";
  }
}

function fullAddress(row: Address): string {
  return [row.province, row.city, row.detail].filter(Boolean).join(" ");
}

onMounted(() => {
  void site.load();
  void load();
});
</script>

<template>
  <div class="container-page py-10 lg:py-14">
    <Breadcrumbs :items="crumbs" />

    <div class="mt-4 flex flex-wrap items-end justify-between gap-4">
      <SectionHeading
        :level="1"
        eyebrow="Addresses"
        title="收货地址"
        description="地址只用于发货与售后寄回。订单会保存独立快照，之后修改或删除地址都不会改变历史订单。"
      />
      <div class="flex flex-wrap items-center gap-3">
        <StatusBadge
          :tone="atLimit ? 'amber' : 'neutral'"
          :label="`${addresses.length} / ${MAX_ADDRESSES} 条`"
        />
        <AppButton
          size="sm"
          :disabled="atLimit"
          :disabled-reason="atLimit ? `已达上限 ${MAX_ADDRESSES} 条，请先删除不再使用的地址。` : ''"
          @click="openCreate()"
        >
          新增地址
        </AppButton>
      </div>
    </div>

    <p v-if="savedNotice" class="mt-4 rounded-sm border border-jade bg-jade-soft px-3 py-2 text-sm text-jade" role="status">
      {{ savedNotice }}
    </p>

    <!-- 表单 -->
    <SurfaceCard v-if="formOpen" as="section" class="mt-6" aria-labelledby="address-form-heading">
      <h2 id="address-form-heading" class="font-display text-xl">
        {{ editing ? "编辑地址" : "新增地址" }}
      </h2>
      <p class="mt-2 text-xs text-ink-faint">
        {{ editing ? "手机号只以脱敏形式保存与展示，修改时请重新录入完整号码。" : "请填写可实际签收的地址；代收点需写明网点与取件码。" }}
      </p>

      <form class="mt-5 grid gap-4 sm:grid-cols-2" novalidate @submit.prevent="submit">
        <FieldRow label="地址标签" for="address-label" hint="例如：家、公司、代收点。">
          <input
            id="address-label"
            v-model="form.label"
            class="field-base"
            type="text"
            maxlength="30"
            @input="markDirty"
          />
        </FieldRow>

        <FieldRow label="收件人" for="address-recipient" required :error="fieldErrors['recipient']">
          <input
            id="address-recipient"
            v-model="form.recipient"
            class="field-base"
            type="text"
            maxlength="60"
            autocomplete="name"
            @input="markDirty"
          />
        </FieldRow>

        <FieldRow
          label="联系电话"
          for="address-phone"
          required
          :error="fieldErrors['phone']"
          :hint="editing ? '当前已保存：' + (addresses.find((item) => item.id === form.id)?.phone_masked ?? '—') : ''"
        >
          <input
            id="address-phone"
            v-model.trim="form.phone"
            class="field-base tabular-nums"
            type="tel"
            inputmode="numeric"
            maxlength="11"
            autocomplete="tel-national"
            @input="markDirty"
          />
        </FieldRow>

        <FieldRow label="邮政编码（可选）" for="address-postal">
          <input
            id="address-postal"
            v-model.trim="form.postal_code"
            class="field-base tabular-nums"
            type="text"
            inputmode="numeric"
            maxlength="20"
            autocomplete="postal-code"
            @input="markDirty"
          />
        </FieldRow>

        <FieldRow label="省 / 自治区 / 直辖市" for="address-province">
          <input
            id="address-province"
            v-model="form.province"
            class="field-base"
            type="text"
            maxlength="40"
            autocomplete="address-level1"
            @input="markDirty"
          />
        </FieldRow>

        <FieldRow label="城市 / 区县" for="address-city">
          <input
            id="address-city"
            v-model="form.city"
            class="field-base"
            type="text"
            maxlength="40"
            autocomplete="address-level2"
            @input="markDirty"
          />
        </FieldRow>

        <div class="sm:col-span-2">
          <FieldRow
            label="详细地址"
            for="address-detail"
            required
            :error="fieldErrors['detail']"
            hint="街道、门牌号、楼栋与房间号；4–200 个字符。"
          >
            <textarea
              id="address-detail"
              v-model="form.detail"
              class="field-base min-h-[5rem]"
              maxlength="200"
              autocomplete="street-address"
              @input="markDirty"
            />
          </FieldRow>
        </div>

        <label class="flex items-start gap-3 text-sm text-ink-soft sm:col-span-2">
          <input v-model="form.is_default" type="checkbox" class="mt-1 size-4 shrink-0 accent-amber" @change="markDirty" />
          <span>设为默认地址（结算时默认选中；同一时间只有一条默认地址）。</span>
        </label>

        <p v-if="formError" class="rounded-sm border border-wine bg-wine-soft px-3 py-2 text-sm text-wine sm:col-span-2" role="alert">
          {{ formError }}
        </p>
        <p v-else-if="savedAt" class="text-sm text-jade sm:col-span-2" role="status">已保存。</p>

        <div class="flex flex-wrap items-center gap-3 sm:col-span-2">
          <AppButton type="submit" :loading="saving">保存地址</AppButton>
          <AppButton variant="secondary" @click="formOpen = false">取消</AppButton>
          <span class="text-xs text-ink-faint">
            状态：{{ saving ? "保存中" : dirty ? "有未保存修改" : "未修改" }}
          </span>
          <span v-if="!dirty" class="text-xs text-ink-faint">没有修改时保存不会覆盖服务端数据。</span>
        </div>
      </form>
    </SurfaceCard>

    <!-- 列表 -->
    <div class="mt-8">
      <SkeletonBlock v-if="loading" :lines="5" aria-label="正在读取收货地址" />

      <EmptyState
        v-else-if="sessionExpired"
        tone="error"
        title="登录状态已过期"
        description="为保护地址信息，请重新登录后再查看。"
      >
        <AppButton size="sm" to="/login?returnTo=/account/addresses">重新登录</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="forbidden"
        tone="forbidden"
        title="当前账号无权查看地址簿"
        description="请确认登录的账号；如需协助，请通过公开客服邮箱联系。"
      />

      <EmptyState
        v-else-if="loadError"
        tone="error"
        title="地址加载失败"
        :description="`${loadError} 网络恢复后可以重试。`"
      >
        <AppButton size="sm" @click="load()">重新加载</AppButton>
      </EmptyState>

      <EmptyState
        v-else-if="addresses.length === 0"
        title="还没有保存地址"
        description="结算时可以直接填写地址；保存后会在这里管理，最多 20 条。"
      >
        <AppButton size="sm" @click="openCreate()">新增第一条地址</AppButton>
      </EmptyState>

      <ul v-else class="grid gap-4 sm:grid-cols-2">
        <li v-for="row in addresses" :key="row.id">
          <SurfaceCard class="flex h-full flex-col">
            <div class="flex flex-wrap items-center gap-2">
              <StatusBadge tone="neutral" :label="row.label || '地址'" />
              <StatusBadge v-if="row.is_default" tone="jade" label="默认地址" />
            </div>
            <p class="mt-3 text-sm font-medium">
              {{ row.recipient }} · <span class="tabular-nums">{{ row.phone_masked }}</span>
            </p>
            <p class="mt-1 text-sm text-ink-muted">{{ fullAddress(row) }}</p>
            <p v-if="row.postal_code" class="mt-1 text-xs text-ink-faint tabular-nums">邮编 {{ row.postal_code }}</p>

            <div
              v-if="deletingId === row.id"
              class="mt-4 rounded-sm border border-wine bg-wine-soft p-3 text-xs text-wine"
              role="alert"
            >
              <p class="font-medium">确认删除「{{ row.label || fullAddress(row) }}」？</p>
              <p class="mt-1 leading-relaxed">
                删除后该地址不再出现在结算页；<strong>订单内已保存的地址快照不受影响</strong>，
                历史订单与售后仍按当时的收件信息处理。此操作不可撤销。
              </p>
              <div class="mt-3 flex flex-wrap gap-2">
                <AppButton size="sm" variant="danger" :loading="busyId === row.id" @click="confirmDelete(row)">
                  确认删除
                </AppButton>
                <AppButton size="sm" variant="secondary" @click="deletingId = ''">取消</AppButton>
              </div>
            </div>

            <div v-else class="mt-4 flex flex-wrap gap-2 pt-1">
              <AppButton size="sm" variant="secondary" @click="openEdit(row)">编辑</AppButton>
              <AppButton
                size="sm"
                variant="secondary"
                :loading="busyId === row.id"
                :disabled="row.is_default"
                :disabled-reason="row.is_default ? '该地址已是默认地址。' : ''"
                @click="setDefault(row)"
              >
                设为默认
              </AppButton>
              <AppButton size="sm" variant="quiet" @click="askDelete(row)">删除</AppButton>
            </div>
          </SurfaceCard>
        </li>
      </ul>

      <p v-if="atLimit" class="mt-4 rounded-sm border border-amber bg-amber-soft px-3 py-2 text-sm text-amber-strong" role="status">
        已达到 {{ MAX_ADDRESSES }} 条上限。新增前请先删除不再使用的地址；服务端也会拒绝对超限的创建请求。
      </p>
    </div>

    <p class="mt-8 text-xs text-ink-faint">
      地址中的收件人与电话属于个人信息，只用于发货、售后寄回与法定留证；客服默认看到脱敏信息，
      需要完整地址时按最小必要授权并记录访问。相关说明见
      <RouterLink class="link-underline" to="/legal/privacy">隐私政策</RouterLink>。
    </p>
  </div>
</template>
