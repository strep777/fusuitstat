<script setup lang="ts">
/**
 * 审计前后快照对比（只显示摘要）。
 *
 * 规则（10/11 文档）：审计页面不展示密钥、会话标识、签名 URL、手机号与地址明文；
 * 这里对敏感键名做统一脱敏，并且只输出「字段 → 旧值 → 新值」的摘要，不输出完整报文。
 */
import { computed, ref } from "vue";

const props = withDefaults(
  defineProps<{
    before?: Record<string, unknown> | null;
    after?: Record<string, unknown> | null;
    title?: string;
    /** 额外需要脱敏的键名（小写匹配）。 */
    redactKeys?: string[];
    initiallyOpen?: boolean;
  }>(),
  { before: null, after: null, title: "变更摘要", redactKeys: () => [], initiallyOpen: false },
);

/** 用哨兵值区分「字段缺失」与「值就是 null」。 */
const MISSING = "__json_diff_missing__";

const SENSITIVE_PATTERNS = [
  "secret",
  "password",
  "passwd",
  "token",
  "signature",
  "sign",
  "cookie",
  "session",
  "csrf",
  "phone",
  "mobile",
  "tel",
  "address",
  "id_card",
  "idcard",
  "bank",
  "card_no",
  "private_key",
  "otp",
  "code_hash",
  "recovery",
];

const MASK = "（已脱敏）";

export type ChangeKind = "added" | "removed" | "modified";

export interface DiffEntry {
  key: string;
  before: unknown;
  after: unknown;
  kind: ChangeKind;
  sensitive: boolean;
}

function isSensitive(key: string): boolean {
  const lower = key.toLowerCase();
  if (props.redactKeys.some((item) => item.toLowerCase() === lower)) return true;
  return SENSITIVE_PATTERNS.some((pattern) => lower.includes(pattern));
}

function normalize(value: unknown): string {
  if (value === undefined) return MISSING;
  if (value === null) return "null";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return "（无法序列化的值）";
  }
}

function display(value: string, sensitive: boolean): string {
  if (sensitive) return MASK;
  if (value === MISSING) return "—";
  if (value === "null") return "空值";
  if (value.length > 120) return `${value.slice(0, 117)}…（已截断）`;
  return value;
}

const entries = computed<DiffEntry[]>(() => {
  const before = props.before ?? {};
  const after = props.after ?? {};
  const keys = Array.from(new Set([...Object.keys(before), ...Object.keys(after)])).sort();
  const result: DiffEntry[] = [];
  for (const key of keys) {
    const oldValue = normalize(before[key]);
    const newValue = normalize(after[key]);
    if (oldValue === newValue) continue;
    const kind: ChangeKind =
      oldValue === MISSING ? "added" : newValue === MISSING ? "removed" : "modified";
    result.push({ key, before: oldValue, after: newValue, kind, sensitive: isSensitive(key) });
  }
  return result;
});

const LIMIT = 40;
const expanded = ref(props.initiallyOpen);
const visible = computed(() => (expanded.value ? entries.value : entries.value.slice(0, 12)));
const hiddenCount = computed(() => Math.max(0, entries.value.length - visible.value.length));

const KIND_LABEL: Record<ChangeKind, string> = { added: "新增", removed: "删除", modified: "修改" };

function beforeText(entry: DiffEntry): string {
  return display(String(entry.before), entry.sensitive);
}

function afterText(entry: DiffEntry): string {
  return display(String(entry.after), entry.sensitive);
}
</script>

<template>
  <section class="rounded-lg border border-line bg-surface">
    <header class="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
      <div>
        <h3 class="text-sm font-medium">{{ title }}</h3>
        <p class="text-xs text-ink-faint">
          共 {{ entries.length }} 处变化
          <span v-if="entries.some((entry) => entry.sensitive)" class="ml-2">
            含敏感字段，已按规范脱敏显示
          </span>
        </p>
      </div>
      <button
        v-if="entries.length > 12"
        type="button"
        class="rounded-xs px-2 py-1 text-xs text-ink-muted underline decoration-line-strong underline-offset-2 hover:text-amber-strong"
        @click="expanded = !expanded"
      >
        {{ expanded ? "只看摘要" : `展开全部 ${Math.min(entries.length, LIMIT)} 条` }}
      </button>
    </header>

    <p v-if="!entries.length" class="px-4 py-4 text-sm text-ink-muted" role="status">
      前后快照没有差异，或该动作没有记录字段级变化。
    </p>

    <div v-else class="overflow-x-auto">
      <table class="w-full border-collapse text-xs">
        <caption class="sr-only">审计前后快照字段差异</caption>
        <thead>
          <tr class="border-b border-line bg-surface-muted text-ink-muted">
            <th scope="col" class="px-4 py-2 text-left font-medium">字段</th>
            <th scope="col" class="px-4 py-2 text-left font-medium">变更前</th>
            <th scope="col" class="px-4 py-2 text-left font-medium">变更后</th>
            <th scope="col" class="px-4 py-2 text-left font-medium">类型</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="entry in visible" :key="entry.key" class="border-b border-line last:border-b-0">
            <td class="px-4 py-2 align-top">
              <span class="font-mono text-ink">{{ entry.key }}</span>
              <span v-if="entry.sensitive" class="ml-2 text-ink-faint">敏感</span>
            </td>
            <td class="px-4 py-2 align-top text-ink-muted">{{ beforeText(entry) }}</td>
            <td class="px-4 py-2 align-top text-ink-soft">{{ afterText(entry) }}</td>
            <td class="px-4 py-2 align-top">
              <span
                class="rounded-xs border border-line px-1.5 py-0.5"
                :class="entry.kind === 'removed' ? 'text-wine' : entry.kind === 'added' ? 'text-jade' : 'text-amber-strong'"
              >
                {{ KIND_LABEL[entry.kind] }}
              </span>
            </td>
          </tr>
        </tbody>
      </table>
      <p v-if="hiddenCount" class="px-4 py-2 text-xs text-ink-faint">
        还有 {{ hiddenCount }} 处变化未显示，展开后可查看。
      </p>
    </div>
  </section>
</template>
