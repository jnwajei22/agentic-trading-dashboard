import test from "node:test"
import assert from "node:assert/strict"
import { resolveBackendBaseUrl } from "../lib/afd/backend-runtime"
import { forwardToWorkspace } from "../lib/afd/backend"

test("engine binding ignores retired backend variables and validates target", () => {
  assert.throws(() => resolveBackendBaseUrl({ NODE_ENV: "production", BACKEND_API_BASE_URL: "https://retired.example.com" }))
  assert.throws(() => resolveBackendBaseUrl({ DESK_ENGINE_URL: "https://user:secret@example.com" }))
  assert.throws(() => resolveBackendBaseUrl({ DESK_ENGINE_URL: "https://example.com/api" }))
  assert.equal(resolveBackendBaseUrl({ DESK_ENGINE_URL: "http://engine.internal/" }), "http://engine.internal")
  assert.equal(resolveBackendBaseUrl({ NODE_ENV: "development" }), "http://127.0.0.1:8000")
})

test("shared forwarding sends no bearer token and preserves sanitized errors", async () => {
  const previous = globalThis.fetch
  process.env.DESK_ENGINE_URL = "https://engine.internal"
  globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
    assert.equal(new Headers(init?.headers).get("Authorization"), null)
    return new Response(JSON.stringify({ detail: { error: "invalid", message: "Rejected", password: "secret" } }), { status: 422, headers: { "content-type": "application/json" } })
  }) as typeof fetch
  await assert.rejects(() => forwardToWorkspace("/api/me"), (error: unknown) => {
    const serialized = JSON.stringify(error)
    assert.doesNotMatch(serialized, /private-token|secret/)
    return true
  })
  globalThis.fetch = previous
})
