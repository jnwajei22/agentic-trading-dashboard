export type FailureCategory = "configuration" | "authentication" | "unauthorized" | "not_found" | "timeout" | "dns" | "tls" | "server_error" | "http_error" | "network"
type RuntimeEnv = Record<string, string | undefined>

/** Internal service binding only. The retired external backend URL is never read. */
export function resolveBackendBaseUrl(env: RuntimeEnv = process.env) {
  const configured = env.DESK_ENGINE_URL?.trim().replace(/\/$/, "")
    || (env.NODE_ENV !== "production" ? "http://127.0.0.1:8000" : "")
  if (!configured) throw new Error("desk_engine_binding_not_configured")
  const target = new URL(configured)
  if (!/^https?:$/.test(target.protocol) || target.username || target.password || target.search || target.hash || target.pathname !== "/") throw new Error("desk_engine_binding_invalid")
  return target.origin
}

export function classifyNetworkError(error: unknown): FailureCategory {
  const value = error as { name?: string; code?: string; cause?: { code?: string } }
  const code = value?.cause?.code ?? value?.code ?? ""
  if (value?.name === "AbortError" || value?.name === "TimeoutError") return "timeout"
  if (["ENOTFOUND", "EAI_AGAIN"].includes(code)) return "dns"
  if (code.startsWith("CERT_") || code.includes("TLS")) return "tls"
  return "network"
}

export function classifyStatus(status: number): FailureCategory {
  if (status === 401 || status === 403) return "unauthorized"
  if (status === 404) return "not_found"
  if (status >= 500) return "server_error"
  return "http_error"
}
