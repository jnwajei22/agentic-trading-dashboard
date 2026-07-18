import test from "node:test"
import assert from "node:assert/strict"
import { afdBackendPath, isAllowedAfdRoute } from "../lib/afd/routes"

test("proxy allowlist accepts only required path and method pairs", () => {
  assert.equal(isAllowedAfdRoute("me", "GET"), true)
  assert.equal(isAllowedAfdRoute("execution-profiles/p_1", "PATCH"), true)
  assert.equal(isAllowedAfdRoute("accounts/demo/instruments", "GET"), true)
  assert.equal(isAllowedAfdRoute("autonomous-schedules/s_1/pause", "POST"), true)
  assert.equal(isAllowedAfdRoute("autonomous-schedule-runs/d_1/retry", "POST"), true)
  assert.equal(isAllowedAfdRoute("me", "DELETE"), false)
  assert.equal(isAllowedAfdRoute("execution-profiles/p_1/autonomy/status", "POST"), false)
  assert.equal(isAllowedAfdRoute("execution-profiles/p_1/autonomy/schedule", "GET"), false)
  assert.equal(isAllowedAfdRoute("admin/secrets", "GET"), false)
})

test("query strings and encoded path values are preserved safely", () => {
  assert.equal(afdBackendPath(["accounts", "demo account", "instruments"], "?search=EUR%2FUSD&tradable=true"), "/api/accounts/demo%20account/instruments?search=EUR%2FUSD&tradable=true")
})
