import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { resolveBackendBaseUrl } from "../lib/afd/backend-runtime"
import { isAllowedAfdRoute } from "../lib/afd/routes"

const source = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8")

test("server configuration prefers the private backend variable with legacy compatibility", () => {
  assert.equal(resolveBackendBaseUrl({ BACKEND_API_BASE_URL:"https://new.example", NEXT_PUBLIC_API_BASE_URL:"https://old.example" }), "https://new.example")
  assert.equal(resolveBackendBaseUrl({ NEXT_PUBLIC_API_BASE_URL:"https://old.example" }), "https://old.example")
  assert.doesNotMatch(source("lib/afd/backend-runtime.ts"), /window\.|document\./)
})

test("strict proxy permits generic provider contracts without becoming generic", () => {
  assert.equal(isAllowedAfdRoute("providers", "GET"), true)
  assert.equal(isAllowedAfdRoute("trading/accounts/account_1/primary", "PUT"), true)
  assert.equal(isAllowedAfdRoute("trading/connections/conn_1", "DELETE"), true)
  assert.equal(isAllowedAfdRoute("trading/connections/conn_1", "PATCH"), false)
})

test("connection cards distinguish broker platform chart and signal roles", () => {
  const settings = source("app/(desk)/settings/broker/settings.tsx")
  assert.match(settings, /Connected through/)
  assert.match(settings, /TradingView is not a broker/)
  assert.match(settings, /Charts and Signals/)
  assert.match(settings, /Robinhood Agentic MCP boundary only/)
  assert.match(settings, /Coming Later|Not Configured/)
})

test("Markets selection is canonical interactive and race safe", () => {
  const page = source("app/(desk)/markets/page.tsx")
  const workspace = source("app/(desk)/markets/workspace.tsx")
  assert.match(page, /forex:EUR\/USD/)
  assert.match(workspace, /canonical_id/)
  assert.match(workspace, /router\.replace\(`\/markets\?instrument=/)
  assert.match(workspace, /searchParams\.get\("instrument"\)/)
  assert.match(workspace, /AbortController/)
  assert.match(workspace, /sequence !== requestSequence\.current/)
  assert.match(workspace, /key=\{tradingViewSymbol\}/)
  assert.doesNotMatch(workspace, /router\.refresh/)
})

test("Markets updates dependent sections and never converts missing values to zero", () => {
  const workspace = source("app/(desk)/markets/workspace.tsx")
  for (const path of ["/summary", "markets/news", "markets/calendar", "markets/macro", "/tradability/"]) assert.match(workspace, new RegExp(path.replaceAll("/", "\\/")))
  assert.match(workspace, /Promise\.allSettled/)
  assert.match(workspace, /Unavailable/)
  assert.doesNotMatch(workspace, /\?\?\s*0/)
  assert.match(workspace, /Source:/)
})

test("default watchlist and search UX remain populated and actionable", () => {
  const workspace = source("app/(desk)/markets/workspace.tsx")
  assert.match(workspace, /No matching markets/)
  assert.match(workspace, /This watchlist is empty/)
  assert.match(workspace, /aria-current=\{active\}/)
  assert.match(workspace, /setTimeout/)
  assert.match(workspace, /Clear search/)
})
