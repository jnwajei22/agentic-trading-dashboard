import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { workspaceSession } from "../lib/afd/session"
const source = (path: string) => readFileSync(path, "utf8")
test("workspace has no login session or redirects", async () => {
  assert.deepEqual(await workspaceSession(), { user: { name: "Shared workspace", email: null } })
  for (const path of ["lib/afd/session.ts", "lib/afd/backend.ts", "app/api/afd/[...path]/route.ts", "app/api/assistant/stream/route.ts"]) {
    assert.doesNotMatch(source(path), /auth0|getAccessToken|getSession|Bearer/)
  }
  assert.match(source("app/login/page.tsx"), /redirect\("\/dashboard"\)/)
})
test("onboarding remains dynamic and available without login", () => {
  for (const path of ["app/connect-tradelocker/page.tsx", "app/select-account/page.tsx"]) {
    assert.match(source(path), /export const dynamic = ["']force-dynamic["']/)
  }
  assert.match(source("app/connect-tradelocker/page.tsx"), /<BrokerConnectForm/)
})
test("engine is private in the same Vercel project", () => {
  const config = JSON.parse(source("vercel.json"))
  assert.equal(config.services.engine.root, "server")
  assert.equal(config.services.dashboard.bindings[0].service, "engine")
  assert.ok(config.rewrites.every((route: { destination: { service: string } }) => route.destination.service === "dashboard"))
})
