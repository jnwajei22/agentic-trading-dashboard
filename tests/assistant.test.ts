import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { consumeAssistantStream } from "../lib/afd/assistant"
import { isAllowedAfdRoute } from "../lib/afd/routes"

test("SSE decoder handles split multibyte and partial frames", async () => {
  const bytes = new TextEncoder().encode('data: {"type":"delta","text":"€"}\n\ndata: {"type":"done"}\n\n')
  const body = new ReadableStream<Uint8Array>({ start(c) { for (const b of bytes) c.enqueue(new Uint8Array([b])); c.close() } })
  const events: object[] = []; await consumeAssistantStream(body, e => events.push(e))
  assert.deepEqual(events, [{ type: "delta", text: "€" }, { type: "done" }])
})
test("truncated stream is rejected", async () => {
  const body = new ReadableStream<Uint8Array>({ start(c) { c.enqueue(new TextEncoder().encode('data: {')); c.close() } })
  await assert.rejects(consumeAssistantStream(body, () => {}))
})
test("approval proxy is narrowly scoped and MCP routes are retired", () => {
  assert.ok(isAllowedAfdRoute(`assistant/conversations/chat_${"a".repeat(32)}/approvals/msg_${"b".repeat(32)}`, "POST"))
  assert.ok(!isAllowedAfdRoute("assistant/conversations/other/approvals/forged", "POST"))
  assert.ok(!isAllowedAfdRoute("integrations/mcp", "GET"))
})
test("client assistant has no provider secret or direct model calls", () => {
  const client = readFileSync(new URL("../components/desk/assistant-panel.tsx", import.meta.url), "utf8")
  assert.doesNotMatch(client, /OPENAI_API_KEY|NEXT_PUBLIC_.*KEY|api\.openai\.com|dangerouslySetInnerHTML/)
  const proxy = readFileSync(new URL("../app/api/assistant/stream/route.ts", import.meta.url), "utf8")
  assert.doesNotMatch(proxy, /auth0|getAccessToken|Bearer/)
  assert.match(proxy, /new Response\(upstream.body/)
  assert.match(proxy, /isSameOriginRequest\(request\)/)
})
