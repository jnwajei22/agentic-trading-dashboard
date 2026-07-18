import test from "node:test"
import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import { join } from "node:path"

const root = process.cwd()
const source = (path: string) => readFileSync(join(root, path), "utf8")

test("authentication-sensitive onboarding pages are force-dynamic", () => {
  for (const path of ["app/connect-tradelocker/page.tsx", "app/select-account/page.tsx", "app/login/page.tsx"]) {
    assert.match(source(path), /export const dynamic = ["']force-dynamic["']/)
  }
})

test("session helper redirects unauthenticated users through Auth0 with returnTo", () => {
  const session = source("lib/afd/session.ts")
  assert.match(session, /await auth0\.getSession\(\)/)
  assert.match(session, /\/auth\/login\?returnTo=\$\{encodeURIComponent\(returnTo\)\}/)
})

test("authenticated connect route requires a session and renders its onboarding form", () => {
  const connect = source("app/connect-tradelocker/page.tsx")
  assert.match(connect, /await requireSession\(["']\/connect-tradelocker["']\)/)
  assert.match(connect, /<BrokerConnectForm/)
})

test("no session error is masked as an unauthenticated state", () => {
  const files = [
    "app/page.tsx", "app/login/page.tsx", "app/connect-tradelocker/page.tsx",
    "app/select-account/page.tsx", "app/(desk)/layout.tsx", "lib/afd/backend.ts", "lib/afd/session.ts",
  ]
  for (const path of files) assert.doesNotMatch(source(path), /getSession\(\)\.catch/, path)
})
