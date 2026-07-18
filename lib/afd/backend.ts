import { auth0 } from "./auth"
import { classifyNetworkError, classifyStatus, resolveBackendBaseUrl } from "./backend-runtime"
import { AfdBackendError, backendMessage, safeErrorPayload } from "./errors"

export async function forwardWithAuthorization<T>(path: string, authorization: string, init: RequestInit = {}): Promise<{ data: T; status: number }> {
  let baseUrl: string
  try { baseUrl = resolveBackendBaseUrl() } catch { throw new AfdBackendError(503, "Backend API URL is not configured safely.", "backend_unavailable", {}, "configuration") }
  let response: Response
  try {
    const timeout = AbortSignal.timeout(10_000)
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      cache: "no-store",
      headers: { Authorization: authorization, ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
      signal: init.signal ? AbortSignal.any([init.signal, timeout]) : timeout,
    })
  } catch (error) {
    const category = classifyNetworkError(error)
    console.error("[afd] backend request failed", { path: path.split("?")[0], method: init.method ?? "GET", category })
    throw new AfdBackendError(category === "timeout" ? 504 : 502, "Backend API unavailable.", "backend_unavailable", {}, category)
  }
  const text = await response.text()
  let parsed: unknown = {}
  if (text && response.headers.get("content-type")?.toLowerCase().includes("json")) {
    try { parsed = JSON.parse(text) } catch { parsed = {} }
  }
  const payload = safeErrorPayload(parsed)
  if (!response.ok) throw new AfdBackendError(response.status, backendMessage(payload), "backend_error", payload, classifyStatus(response.status))
  if (!response.headers.get("content-type")?.toLowerCase().includes("json")) throw new AfdBackendError(502, "Backend returned a non-JSON response.")
  return { data: parsed as T, status: response.status }
}

export async function authenticatedBackendClient() {
  const session = await auth0.getSession().catch(() => null)
  if (!session) throw new AfdBackendError(401, "Please log in.", "not_authenticated", {}, "authentication")
  let token: string
  try { ({ token } = await auth0.getAccessToken()) } catch { throw new AfdBackendError(401, "Could not get backend access token.", "token_acquisition_failed", {}, "authentication") }
  const authorization = `Bearer ${token}`
  return async <T>(path: string, init?: RequestInit) => (await forwardWithAuthorization<T>(path, authorization, init)).data
}

export async function serverBackendFetch<T>(path: string, init?: RequestInit) {
  const request = await authenticatedBackendClient()
  return request<T>(path, init)
}
