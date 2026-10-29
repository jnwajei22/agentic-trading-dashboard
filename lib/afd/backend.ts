import { classifyNetworkError, classifyStatus, resolveBackendBaseUrl } from "./backend-runtime"
import { AfdBackendError, backendMessage, safeErrorPayload } from "./errors"

export async function forwardToWorkspace<T>(path: string, init: RequestInit = {}): Promise<{ data: T; status: number }> {
  let baseUrl: string
  try { baseUrl = resolveBackendBaseUrl() } catch { throw new AfdBackendError(503, "Backend API URL is not configured safely.", "backend_unavailable", {}, "configuration") }
  let response: Response
  try {
    // Allow the private Python service and pooled database to wake on a cold start.
    const executionRead = path.startsWith("/api/trading/order-previews") || /^\/api\/execution-profiles\/[^/]+\/demo-test/.test(path)
    const timeout = AbortSignal.timeout(executionRead ? 90_000 : 30_000)
    response = await fetch(`${baseUrl}${path}`, {
      ...init,
      cache: "no-store",
      headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
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

export async function workspaceBackendClient() {
  return async <T>(path: string, init?: RequestInit) => (await forwardToWorkspace<T>(path, init)).data
}

export async function serverBackendFetch<T>(path: string, init?: RequestInit) {
  const request = await workspaceBackendClient()
  return request<T>(path, init)
}
