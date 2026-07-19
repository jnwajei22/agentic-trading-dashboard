import assert from "node:assert/strict"
import { readFileSync } from "node:fs"
import test from "node:test"
import { formatEnum } from "../lib/afd/display"

const source=(path:string)=>readFileSync(new URL(`../${path}`,import.meta.url),"utf8")
test("customer enum formatting preserves approved acronyms",()=>{
  assert.equal(formatEnum("ai_api_usd"),"AI API USD")
  assert.equal(formatEnum("daily_pnl"),"Daily P&L")
  assert.equal(formatEnum("mcp_id"),"MCP ID")
})
test("markets are provider-aggregated and TradeLocker is optional",()=>{
  const page=source("app/(desk)/markets/page.tsx");const workspace=source("app/(desk)/markets/workspace.tsx")
  for(const route of ["markets/news","markets/calendar"])assert.match(page,new RegExp(route))
  assert.doesNotMatch(page,/markets\/macro/)
  assert.match(workspace,/markets\/.*\/summary/);assert.match(workspace,/TradingView/);assert.match(workspace,/tradability/i)
})
test("FRED is consolidated into summary and the instrument-aware calendar",()=>{
  const workspace=source("app/(desk)/markets/workspace.tsx")
  assert.doesNotMatch(workspace,/Macro context|MacroData|markets\/macro/i)
  assert.match(workspace,/summary\?\.drivers/)
  assert.match(workspace,/Relevant to Selected Market/)
  assert.match(workspace,/High Impact/)
  assert.match(workspace,/All Events/)
  assert.match(workspace,/associated_fred_series/)
  assert.match(workspace,/Recent trend/)
  assert.match(workspace,/Stale|Current/)
})
test("dashboard hides worker identifiers behind automation status",()=>{
  const dashboard=source("app/(desk)/dashboard/page.tsx")
  assert.match(dashboard,/Automation Status/);assert.doesNotMatch(dashboard,/worker_id|Worker health/)
})
test("profile and schedule editors use structured customer controls",()=>{
  const profile=source("app/(desk)/autonomous/[profileId]/profile-editor.tsx");const schedules=source("app/(desk)/schedules/schedule-manager.tsx")
  assert.match(profile,/Market Selection/);assert.doesNotMatch(profile,/Schedule policy|Comma-separated IDs/)
  for(const type of ["market_session","recurring_interval","daily","weekly","custom"])assert.match(schedules,new RegExp(type))
  assert.doesNotMatch(schedules,/cron/i)
})
