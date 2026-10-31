import type { FailureCategory } from "./backend-runtime"

export type BackendPayload = Record<string, unknown>
const safeNestedKeys = new Set(["detail", "error", "message", "code", "fields", "request_id", "retryable", "loc", "msg", "type", "ctx", "blocking_reasons", "retry_after_seconds"])

export class AfdBackendError extends Error {
  constructor(
    public status: number,
    message: string,
    public code: "not_authenticated" | "token_acquisition_failed" | "backend_error" | "backend_unavailable" = "backend_error",
    public payload: BackendPayload = {},
    public category: FailureCategory = "http_error",
  ) { super(message); this.name = "AfdBackendError" }
}

export function safeErrorPayload(value: unknown): BackendPayload {
  if (!value || typeof value !== "object" || Array.isArray(value)) return {}
  const source = value as BackendPayload
  const allowed = ["detail", "error", "message", "code", "fields", "request_id", "retryable", "blocking_reasons", "retry_after_seconds"]
  return Object.fromEntries(allowed.filter((key) => key in source).map((key) => [key, sanitizeNested(source[key])]))
}

function sanitizeNested(value: unknown): unknown {
  if (Array.isArray(value)) return value.slice(0, 50).map(sanitizeNested)
  if (!value || typeof value !== "object") return typeof value === "string" ? value.slice(0, 1000) : value
  return Object.fromEntries(Object.entries(value as BackendPayload).filter(([key]) => safeNestedKeys.has(key)).map(([key, field]) => [key, sanitizeNested(field)]))
}

export function backendMessage(payload: BackendPayload) {
  const detail = payload.detail
  const source = detail && typeof detail === "object" && !Array.isArray(detail) ? detail as BackendPayload : payload
  const message = source.message ?? source.error ?? (typeof detail === "string" ? detail : undefined)
  const base = typeof message === "string" && message.trim() ? message : "Backend request failed."
  const reasons = Array.isArray(source.blocking_reasons) ? source.blocking_reasons.filter((item): item is string => typeof item === "string").slice(0, 8) : []
  return reasons.length ? `${base} ${reasons.map(reason => reason.replaceAll("_", " ")).join("; ")}.` : base
}
