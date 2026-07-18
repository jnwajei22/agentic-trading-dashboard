import type { FailureCategory } from "./backend-runtime"

export type BackendPayload = Record<string, unknown>
const safeNestedKeys = new Set(["detail", "error", "message", "code", "fields", "request_id", "retryable", "loc", "msg", "type", "ctx"])

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
  const allowed = ["detail", "error", "message", "code", "fields", "request_id", "retryable"]
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
  return typeof message === "string" && message.trim() ? message : "Backend request failed."
}
