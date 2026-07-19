import assert from "node:assert/strict"
import {readFileSync} from "node:fs"
import test from "node:test"
import type {ActivityEvent} from "../lib/afd/contracts"
import {eventExplanation,eventTitle,groupActivity,matchesActivitySearch} from "../app/(desk)/activity/activity-model"

const source=(path:string)=>readFileSync(new URL(`../${path}`,import.meta.url),"utf8")
const workspace=source("app/(desk)/activity/workspace.tsx");const page=source("app/(desk)/activity/page.tsx")
const event=(overrides:Partial<ActivityEvent>):ActivityEvent=>({id:"event",event_type:"strategy_evaluated",occurred_at:"2026-07-19T17:42:00Z",...overrides})

test("Activity header owns the filtered CSV export action",()=>{
  assert.match(workspace,/PageHeader title="Activity"/);assert.match(workspace,/actions=\{<Button[^>]*onClick=\{csv\}/)
  assert.match(workspace,/Exports the currently filtered activity/);assert.doesNotMatch(page,/Structured audit/i)
  assert.doesNotMatch(workspace,/grid[^\n]*Export CSV/)
})
test("search is debounced and matches only safe user-facing activity fields",()=>{
  const item=event({symbol:"GBP/USD",strategy_name:"Hourly Forex",account_number:"1",outcome:"NO_TRADE",reason_codes:["confidence_below_threshold"]})
  assert.equal(matchesActivitySearch(item,"gbp"),true);assert.equal(matchesActivitySearch(item,"confidence"),true);assert.equal(matchesActivitySearch(item,"private reasoning"),false)
  assert.match(workspace,/placeholder="Search activity…"/);assert.match(workspace,/setTimeout\(\(\)=>\{setSearch/)
})
test("compact filters expand collapse expose active chips and clear explicitly",()=>{
  assert.match(workspace,/aria-expanded=\{filtersOpen\}/);assert.match(workspace,/setFiltersOpen/)
  assert.match(workspace,/activeFilters\.map/);assert.match(workspace,/Clear Filters/);assert.match(workspace,/Filters\{activeFilters\.length/)
  assert.match(workspace,/useState\(""\).*setEnvironment/);assert.match(workspace,/empty="All Environments"/)
})
test("events render chronologically grouped by customer-facing dates",()=>{
  const groups=groupActivity([event({id:"old",occurred_at:"2026-07-18T12:00:00Z"}),event({id:"new",occurred_at:"2026-07-19T12:00:00Z"})],new Date("2026-07-19T18:00:00"))
  assert.equal(groups[0].label,"Today");assert.equal(groups[0].events[0].id,"new");assert.equal(groups[1].label,"Yesterday")
  assert.match(workspace,/groups\.map/);assert.match(workspace,/<section key=\{group\.date\}/)
})
test("feed uses readable event hierarchy account numbers and safe explanations",()=>{
  const blocked=event({event_type:"strategy_evaluated",outcome:"BLOCKED",reason_codes:["minimum_confidence"]})
  assert.equal(eventTitle(blocked),"Strategy Evaluated");assert.equal(eventExplanation(blocked),"Blocked — Minimum Confidence")
  assert.match(workspace,/Account \$\{accountNumber\}/);assert.match(workspace,/View Details/)
  assert.doesNotMatch(workspace,/JSON\.stringify|chain.of.thought|worker_id|stack trace/i)
})
test("structured details expand without raw payload rendering",()=>{
  assert.match(workspace,/DetailsSheet/);assert.match(workspace,/Structured activity details only/)
  for(const label of ["Exact Timestamp","Markets Checked","Confidence","Blocking Reasons","Risk Checks"])assert.match(workspace,new RegExp(label))
  assert.doesNotMatch(workspace,/Object\.entries\(audit\)|<pre|JSON/)
})
test("empty states distinguish no history from filtered zero results",()=>{
  assert.match(workspace,/title="No activity yet"/);assert.match(workspace,/Strategy evaluations, schedule changes, safety actions, and demo trades will appear here/)
  assert.match(workspace,/Run Demo Test/);assert.match(workspace,/Create Schedule/)
  assert.match(workspace,/title="No matching activity"/);assert.match(workspace,/action=\{<Button[^>]*onClick=\{clearFilters\}>Clear Filters/)
})
test("pagination is conditional compact and reset by filtering",()=>{
  assert.match(workspace,/filtered\.length>0&&<div/);assert.match(workspace,/Showing \{page\*PAGE_SIZE\+1\}/)
  assert.match(workspace,/setPage\(0\)/);assert.doesNotMatch(workspace,/Page \{page \+ 1\}/)
})
test("CSV uses the applied result set and user-facing safe columns",()=>{
  assert.match(workspace,/const rows=filtered\.map/)
  for(const column of ["Timestamp","Account","Broker","Environment","Strategy","Event Type","Instrument","Outcome","Explanation","Blocking Reason","Execution Status"])assert.match(workspace,new RegExp(column))
  assert.doesNotMatch(workspace,/const headers=\[[^\]]*(?:account_alias|reasoning)/i)
})
test("mobile filters stack without horizontal overflow and retry preserves controls",()=>{
  assert.match(workspace,/flex-col[^\n]*sm:flex-row/);assert.doesNotMatch(workspace,/overflow-x-auto|grid-cols-7/)
  assert.match(page,/Promise\.allSettled/);assert.match(workspace,/>Retry<\/Button>/);assert.match(workspace,/ActivitySkeleton/)
})
