import assert from "node:assert/strict"
import test from "node:test"
import { backendMessage, safeErrorPayload } from "../lib/afd/errors"

test("execution blockers survive sanitization while broker secrets are discarded", () => {
  const payload = safeErrorPayload({ detail: { message: "Order review blocked.", blocking_reasons: ["pair_not_allowed"], password: "private", connection: { password: "private" } } })
  assert.equal(backendMessage(payload), "Order review blocked. pair not allowed.")
  assert.doesNotMatch(JSON.stringify(payload), /private|password|connection/)
})
