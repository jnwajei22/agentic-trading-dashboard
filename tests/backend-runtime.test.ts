import test from "node:test"
import assert from "node:assert/strict"
import { resolveBackendBaseUrl } from "../lib/afd/backend-runtime"
import { forwardWithAuthorization } from "../lib/afd/backend"

test("backend URL validation rejects unsafe production targets", () => {
  assert.throws(() => resolveBackendBaseUrl({ NODE_ENV: "production", NEXT_PUBLIC_API_BASE_URL: "http://localhost:8000" }))
  assert.throws(() => resolveBackendBaseUrl({ NODE_ENV: "production", NEXT_PUBLIC_API_BASE_URL: "http://api.example.com" }))
  assert.equal(resolveBackendBaseUrl({ NODE_ENV: "production", NEXT_PUBLIC_API_BASE_URL: "https://api.example.com/" }), "https://api.example.com")
})

test("authenticated forwarding preserves bearer token, status, and sanitized errors", async () => {
  const previous = globalThis.fetch
  process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.com"
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(new Headers(init?.headers).get("Authorization"), "Bearer private-token")
    return new Response(JSON.stringify({ detail: { error: "invalid", message: "Rejected", password: "secret" } }), { status: 422, headers: { "content-type": "application/json" } })
  }) as typeof fetch
  await assert.rejects(() => forwardWithAuthorization("/api/me", "Bearer private-token"), (error: unknown) => {
    const serialized = JSON.stringify(error)
    assert.doesNotMatch(serialized, /private-token|secret/)
    return true
  })
  globalThis.fetch = previous
})
