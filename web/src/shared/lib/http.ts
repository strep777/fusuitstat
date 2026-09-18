/**
 * 集中 fetch 客户端（04 文档：Pinia + 集中 fetch 客户端）。
 *
 * 职责：统一错误形状解析、CSRF 头注入、幂等键、超时、401/403/404/409 的稳定分支。
 * 前端只根据 `error.code` 分支，不对 message 字符串做逻辑判断（06 文档）。
 */

export interface ApiErrorPayload {
  code: string;
  message: string;
  fields: Record<string, string>;
  trace_id: string;
}

export class ApiError extends Error {
  readonly code: string;
  readonly status: number;
  readonly fields: Record<string, string>;
  readonly traceId: string;

  constructor(status: number, payload: ApiErrorPayload) {
    super(payload.message);
    this.name = "ApiError";
    this.status = status;
    this.code = payload.code;
    this.fields = payload.fields ?? {};
    this.traceId = payload.trace_id ?? "";
  }

  /** 需要重新登录 */
  get needsLogin(): boolean {
    return this.code === "AUTH_REQUIRED" || this.code === "SESSION_EXPIRED" || this.status === 401;
  }

  /** 版本冲突：提示刷新后重新提交 */
  get isVersionConflict(): boolean {
    return this.code === "VERSION_CONFLICT";
  }

  /** 支付结果未确认：不要渲染成已取消 */
  get isPaymentUnknown(): boolean {
    return this.code === "PAYMENT_UNKNOWN" || this.code === "PAYMENT_PENDING";
  }
}

export interface RequestOptions {
  method?: "GET" | "POST" | "PATCH" | "PUT" | "DELETE";
  body?: unknown;
  /** 幂等键：创建订单/支付/退款、接受报价、反馈提交、积分调整 */
  idempotencyKey?: string;
  /** 乐观锁：内容与业务编辑 */
  version?: number;
  signal?: AbortSignal;
  timeoutMs?: number;
  /** 表单上传 */
  formData?: FormData;
  /** 后台会话：使用独立 cookie 与路径 */
  admin?: boolean;
  query?: Record<string, string | number | boolean | undefined | null>;
}

const DEFAULT_TIMEOUT = 20_000;

let csrfToken: string | null = null;
let csrfExpiresAt = 0;

export function setCsrfToken(token: string, ttlSeconds = 1800): void {
  csrfToken = token;
  csrfExpiresAt = Date.now() + ttlSeconds * 1000;
}

export function clearCsrfToken(): void {
  csrfToken = null;
  csrfExpiresAt = 0;
}

function newRequestKey(): string {
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (value) => value.toString(16).padStart(2, "0")).join("");
}

export function newIdempotencyKey(): string {
  return newRequestKey();
}

function buildUrl(path: string, query?: RequestOptions["query"]): string {
  const base = path.startsWith("/api/") ? path : `/api/v1${path.startsWith("/") ? path : `/${path}`}`;
  if (!query) return base;
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value === undefined || value === null || value === "") continue;
    params.set(key, String(value));
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

/** 取一个可用的 CSRF token：命中内存缓存则直接用，否则向服务端要一个。 */
async function ensureCsrfToken(force = false): Promise<string | null> {
  if (!force && csrfToken && Date.now() < csrfExpiresAt - 5_000) return csrfToken;
  try {
    const response = await fetch("/api/v1/session/csrf", {
      credentials: "include",
      headers: { Accept: "application/json" },
    });
    if (!response.ok) return null;
    const payload = (await response.json()) as { csrf_token: string; expires_in: number };
    setCsrfToken(payload.csrf_token, payload.expires_in);
    return payload.csrf_token;
  } catch {
    return null;
  }
}

/**
 * 取 CSRF token 的处理说明。
 *
 * 服务端 `GET /api/v1/session/csrf` 在**已登录**时返回绑定该会话的 token（并轮换会话密钥），
 * 未登录时返回匿名 token。整页刷新后内存里的 token 会丢失，这里重新取一次即可；
 * 若仍被 `CSRF_INVALID` 拒绝（其他标签页轮换过），`apiRequest` 会再自愈一次。
 */
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const method = options.method ?? "GET";
  const isWrite = method !== "GET";
  const headers: Record<string, string> = { Accept: "application/json" };

  let body: BodyInit | undefined;
  if (options.formData) {
    body = options.formData;
  } else if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify(options.body);
  }

  if (isWrite) {
    // 幂等键在重试之间复用：服务端按它去重，重试不会产生第二次业务效果（06 文档）。
    headers["Idempotency-Key"] = options.idempotencyKey ?? newRequestKey();
  }
  if (options.version !== undefined) headers["If-Match"] = String(options.version);

  const send = async (csrf: string | null): Promise<Response> => {
    const outgoing = { ...headers };
    if (isWrite && csrf) outgoing["X-CSRF-Token"] = csrf;
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), options.timeoutMs ?? DEFAULT_TIMEOUT);
    if (options.signal) {
      options.signal.addEventListener("abort", () => controller.abort(), { once: true });
    }
    try {
      return await fetch(buildUrl(path, options.query), {
        method,
        headers: outgoing,
        body,
        credentials: "include",
        signal: controller.signal,
      });
    } finally {
      clearTimeout(timeout);
    }
  };

  let response: Response;
  try {
    if (!isWrite) {
      response = await send(null);
    } else {
      response = await send(await ensureCsrfToken());
      // CSRF 失败单独自愈一次：跨标签页或会话轮换会让手里的 token 失效，
      // 此时重取 token 再试一次；其余失败一律不重试。
      if (response.status === 403) {
        const peek = (await response
          .clone()
          .json()
          .catch(() => null)) as { error?: { code?: string } } | null;
        if (peek?.error?.code === "CSRF_INVALID") {
          response = await send(await ensureCsrfToken(true));
        }
      }
    }
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw new ApiError(0, {
        code: "PROVIDER_UNAVAILABLE",
        message: "请求超时或已取消，请检查网络后重试。",
        fields: {},
        trace_id: "",
      });
    }
    throw new ApiError(0, {
      code: "PROVIDER_UNAVAILABLE",
      message: "网络连接失败，请检查网络后重试。",
      fields: {},
      trace_id: "",
    });
  }

  if (response.status === 204) {
    return undefined as T;
  }

  const text = await response.text();
  let payload: unknown = null;
  if (text) {
    try {
      payload = JSON.parse(text);
    } catch {
      payload = null;
    }
  }

  if (!response.ok) {
    const envelope = (payload as { error?: ApiErrorPayload } | null)?.error;
    throw new ApiError(
      response.status,
      envelope ?? {
        code: response.status === 401 ? "AUTH_REQUIRED" : "INTERNAL_ERROR",
        message: "操作失败，请稍后重试。",
        fields: {},
        trace_id: response.headers.get("X-Request-Id") ?? "",
      },
    );
  }

  return payload as T;
}

export const api = {
  get: <T>(path: string, options: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...options, method: "GET" }),
  post: <T>(path: string, body?: unknown, options: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...options, method: "POST", body }),
  patch: <T>(path: string, body?: unknown, options: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...options, method: "PATCH", body }),
  put: <T>(path: string, body?: unknown, options: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...options, method: "PUT", body }),
  delete: <T>(path: string, options: Omit<RequestOptions, "method" | "body"> = {}) =>
    apiRequest<T>(path, { ...options, method: "DELETE" }),
};
