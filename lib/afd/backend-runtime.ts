export type FailureCategory = "configuration" | "authentication" | "unauthorized" | "not_found" | "timeout" | "dns" | "tls" | "server_error" | "http_error" | "network"
type RuntimeEnv = Record<string, string | undefined>

function host(value?: string) {
  if (!value) return null
  try { return new URL(value.startsWith("http") ? value : `https://${value}`).hostname } catch { return null }
}

export function resolveBackendBaseUrl(env: RuntimeEnv = process.env) {
  const configured = env.NEXT_PUBLIC_API_BASE_URL?.trim().replace(/\/$/, "")
  if (!configured) throw new Error("backend_url_not_configured")
  let target: URL
  try { target = new URL(configured) } catch { throw new Error("backend_url_invalid") }
  if (!/^https?:$/.test(target.protocol)) throw new Error("backend_url_invalid_protocol")
  if (target.username || target.password || target.search || target.hash) throw new Error("backend_url_contains_credentials_or_parameters")
  if (env.NODE_ENV === "production") {
    if (["localhost", "127.0.0.1", "::1"].includes(target.hostname)) throw new Error("backend_url_localhost_in_production")
    if (target.protocol !== "https:") throw new Error("backend_url_requires_https")
    const frontendHosts = [host(env.APP_BASE_URL), host(env.AUTH0_BASE_URL), host(env.VERCEL_URL)].filter(Boolean)
    if (frontendHosts.includes(target.hostname)) throw new Error("backend_url_points_to_frontend")
  }
  return target.origin + target.pathname.replace(/\/$/, "")
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
