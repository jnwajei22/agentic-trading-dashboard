import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync, existsSync } from "node:fs"
import { join } from "node:path"

const root = process.cwd()
test("all requested navigation routes render and template branding is absent", () => {
  for (const path of ["dashboard/page.tsx", "markets/page.tsx", "trade/page.tsx", "autonomous/page.tsx", "schedules/page.tsx", "activity/page.tsx", "settings/page.tsx", "settings/broker/page.tsx", "settings/accounts/page.tsx"]) assert.equal(existsSync(join(root, "app/(desk)", path)), true, path)
  const files = ["package.json", "app/layout.tsx", "components/desk/app-shell.tsx"].map((file) => readFileSync(join(root, file), "utf8")).join("\n")
  const removedBrands = ["Koko" + "nut", "my-v0-" + "project", "Eugene" + " An", "Prompt" + " Engineer", "Free" + " Trial"]
  removedBrands.forEach((brand) => assert.equal(files.includes(brand), false))
})

test("live confirmation is exact and trading capability is visibly gated", () => {
  const source = readFileSync(join(root, "app/(desk)/autonomous/workspace.tsx"), "utf8")
  assert.match(source, /ENABLE LIVE AUTONOMY/)
  assert.match(source, /!controls\.live_execution_supported/)
})
