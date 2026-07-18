import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { automationStatus } from "../lib/afd/automation"
import { formatEnum } from "../lib/afd/display"

const source = (path: string) => readFileSync(new URL(`../${path}`, import.meta.url), "utf8")

test("shared formatting keeps product acronyms and currency pairs intact", () => {
  assert.equal(formatEnum("reward_to_risk"), "Reward to Risk")
  assert.equal(formatEnum("adaptive_structure"), "Adaptive Structure")
  assert.equal(formatEnum("eur_usd"), "EUR/USD")
  assert.equal(formatEnum("api_ids"), "API IDs")
})

test("automation status defaults failed or unknown operational state to safe messaging", () => {
  const base = {
    accounts: [],
    profiles: [{ enabled: true }],
    schedules: [{ enabled: true }],
    controls: { global_autonomous_kill_switch: false, demo_autonomous_enabled: true },
    worker: { status: "healthy" },
  } as Parameters<typeof automationStatus>[0]
  assert.equal(automationStatus({ ...base, status: null }).state, "Broker Disconnected")
  assert.equal(automationStatus({ ...base, status: { connected: true }, controls: { ...base.controls, global_autonomous_kill_switch: true } }).state, "Blocked by Kill Switch")
  assert.equal(automationStatus({ ...base, status: { connected: true }, worker: { status: "unknown", workers: [] } }).state, "Needs Attention")
})

test("global account context and command palette are shared customer controls", () => {
  const provider = source("components/desk/account-provider.tsx")
  const palette = source("components/desk/command-palette.tsx")
  assert.match(provider, /createContext|normalizeAccountContext/)
  assert.match(provider, /broker\/accounts\/.*\/default/)
  assert.match(palette, /event\.metaKey\|\|event\.ctrlKey/)
  assert.match(palette, /event\.key\.toLowerCase\(\)===\"k\"/)
  assert.match(palette, /window\.confirm/)
})

test("first-run checklist is derived from backend resources", () => {
  const dashboard = source("app/(desk)/dashboard/page.tsx")
  const checklist = source("components/desk/onboarding-checklist.tsx")
  for (const label of ["Add a trading connection", "Select a trading account", "Create a strategy", "Configure risk", "Create a schedule", "Run a demo test"]) {
    assert.match(dashboard, new RegExp(label))
  }
  assert.match(dashboard, /complete:data\.connections\.some/)
  assert.match(dashboard, /run\.dry_run\|\|run\.trigger_reason===\"demo_test\"/)
  assert.doesNotMatch(checklist, /localStorage|dismiss/i)
})

test("markets use durable watchlists and remain independent from trading accounts", () => {
  const page = source("app/(desk)/markets/page.tsx")
  const workspace = source("app/(desk)/markets/workspace.tsx")
  assert.match(page, /watchlists/)
  assert.match(workspace, /Create watchlist|saveItems|pinned/)
  assert.match(workspace, /TradingView visual context; not authoritative for execution/)
  assert.doesNotMatch(workspace, /No TradeLocker account/)
})

test("profile editor uses templates, market multi-select, and preserves scheduling ownership", () => {
  const editor = source("app/(desk)/autonomous/[profileId]/profile-editor.tsx")
  const profile = source("lib/afd/profile.ts")
  for (const template of ["Conservative Forex", "Balanced Forex", "Aggressive Demo", "Forex Majors", "Metals Focus", "Custom"]) assert.match(editor, new RegExp(template))
  assert.match(editor, /beforeunload/)
  assert.match(editor, /Selected-market|selected market|included_instrument_ids/i)
  assert.doesNotMatch(editor, /comma-separated/i)
  assert.match(profile, /key === "schedule_policy"\) continue/)
})

test("demo tests, schedules, and activity expose structured non-executing UX", () => {
  const autonomous = source("app/(desk)/autonomous/workspace.tsx")
  const schedules = source("app/(desk)/schedules/schedule-manager.tsx")
  const activity = source("app/(desk)/activity/workspace.tsx")
  assert.match(autonomous, /demo-test/)
  assert.match(autonomous, /No order can be submitted/)
  assert.doesNotMatch(autonomous, /JSON\.stringify\(result\.validation/)
  assert.match(schedules, /next_run_times|readiness_warnings|market_aware/)
  assert.match(activity, /Export CSV|Filter by account|occurred_at/)
})

test("settings use trading terminology and confirm credential removal", () => {
  const settings = source("app/(desk)/settings/page.tsx")
  const connections = source("app/(desk)/settings/broker/settings.tsx")
  const preferences = source("app/(desk)/settings/preferences-client.tsx")
  assert.match(settings, /Trading Connections|Trading Accounts|Data and Privacy/)
  assert.match(connections, /Remove Connection|AlertDialog/)
  assert.match(preferences, /user-preferences|Daily summary|timezone/)
})

test("browser code contains neither direct MCP calls nor legacy Trade naming", () => {
  const files = [
    "app/(desk)/dashboard/page.tsx",
    "app/(desk)/markets/workspace.tsx",
    "app/(desk)/trade/workspace.tsx",
    "components/desk/app-shell.tsx",
  ].map(source).join("\n")
  assert.doesNotMatch(files, /Manual Trade/)
  assert.doesNotMatch(files, /mcp\.(call|invoke)|\/mcp\//i)
})
