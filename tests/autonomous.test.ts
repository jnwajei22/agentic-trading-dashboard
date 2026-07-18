import test from "node:test"
import assert from "node:assert/strict"
import { updateControlsWithRollback } from "../lib/afd/autonomous"
import { blockedControls } from "../lib/afd/dashboard-data"

test("autonomous control failure rolls UI back to last backend-confirmed state", async () => {
  let visible = { ...blockedControls, global_autonomous_kill_switch: false }
  await assert.rejects(() => updateControlsWithRollback(blockedControls, { global_autonomous_kill_switch: false }, (value) => { visible = value }, async () => { throw new Error("network") }))
  assert.deepEqual(visible, blockedControls)
})
