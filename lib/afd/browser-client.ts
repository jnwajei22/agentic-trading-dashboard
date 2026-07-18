import { backendMessage, safeErrorPayload, type BackendPayload } from "./errors"

export class BrowserAfdError extends Error {
  constructor(public status: number, message: string, public payload: BackendPayload = {}) { super(message); this.name = "BrowserAfdError" }
}

export async function browserAfdFetch<T>(path: string, init: RequestInit = {}) {
  const normalized = path.replace(/^\/+/, "")
  const response = await fetch(`/api/afd/${normalized}`, {
    ...init, cache: "no-store", credentials: "same-origin",
    headers: { ...(init.body ? { "Content-Type": "application/json" } : {}), ...init.headers },
  })
  const raw = await response.json().catch(() => ({}))
  if (!response.ok) {
    const payload = safeErrorPayload(raw)
    throw new BrowserAfdError(response.status, backendMessage(payload), payload)
  }
  return raw as T
}

export function mutateAfd<T>(path: string, method: "POST" | "PUT" | "PATCH" | "DELETE", body?: object) {
  return browserAfdFetch<T>(path, { method, body: body ? JSON.stringify(body) : undefined })
}
