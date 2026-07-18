import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join } from "node:path"
import { normalizeDiscoveredAccounts } from "../lib/afd/onboarding"

test("multiple discovered TradeLocker accounts are all normalized for rendering", () => {
  const accounts = normalizeDiscoveredAccounts([
    { accountId: "a1", accNum: "101", name: "First", currency: "USD", isDemo: true },
    { accountId: "a2", accNum: "102", name: "Second", currency: "EUR", environment: "live" },
    { accountId: "a3", accNum: "103", name: "Third", currency: "GBP", status: "inactive" },
  ])
  assert.equal(accounts.length, 3)
  assert.deepEqual(accounts.map((account) => account.accountNumber), ["101", "102", "103"])
  assert.equal(accounts[0].environment, "demo")
  assert.equal(accounts[2].available, false)
})

test("account selection occurs only from an explicit click", () => {
  const selector = readFileSync(join(process.cwd(), "app/select-account/selector.tsx"), "utf8")
  assert.match(selector, /discovered\.map/)
  assert.match(selector, /onClick=\{\(\) => select\(account\)\}/)
  assert.doesNotMatch(selector, /useEffect|select\(discovered\[0\]\)|select\(accounts\[0\]\)/)
  assert.match(selector, /router\.replace\(["']\/dashboard\?connected=1["']\)/)
})

test("shell renders every durable account and labels only the explicit default", () => {
  const shell = readFileSync(join(process.cwd(), "components/desk/app-shell.tsx"), "utf8")
  assert.match(shell, /accounts\.map/)
  assert.match(shell, /account\.is_default_analysis \? ["'] · Default["']/)
  assert.doesNotMatch(shell, /is_default_analysis\) \?\? accounts\[0\]/)
})
