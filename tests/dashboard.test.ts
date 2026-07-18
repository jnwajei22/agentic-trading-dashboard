import test from "node:test"
import assert from "node:assert/strict"
import { loadDashboardData } from "../lib/afd/dashboard-data"

test("dashboard normalizes real response wrappers and preserves partial success", async () => {
  const data = await loadDashboardData(async <T>(path: string) => {
    if (path.includes("worker-health")) throw Object.assign(new Error("offline"), { category: "timeout" })
    if (path.endsWith("trading/accounts")) return { accounts: [{ public_id: "a1", account_alias: "demo", profiles: [] }] } as T
    if (path.endsWith("execution-profiles")) return { profiles: [{ public_id: "p1", enabled: true }] } as T
    return {} as T
  })
  assert.equal(data.accounts[0].account_alias, "demo")
  assert.equal(data.profiles.length, 1)
  assert.equal(data.worker.status, "unavailable")
  assert.equal(data.errors.worker, "timeout")
  assert.equal(data.controls.global_autonomous_kill_switch, true)
})
