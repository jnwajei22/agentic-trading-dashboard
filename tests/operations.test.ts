import assert from "node:assert/strict"
import test from "node:test"
import {csvCell} from "../lib/afd/activity-export"
import {formatWorkspaceTime,workspaceDate} from "../lib/afd/preferences"
import {isAllowedAfdRoute} from "../lib/afd/routes"
import {groupActivity} from "../app/(desk)/activity/activity-model"
import type {UserPreferences} from "../lib/afd/contracts"

test("CSV escapes quotes and shields spreadsheet formulas including leading whitespace",()=>{
  for(const value of ['=HYPERLINK("https://invalid")',' +SUM(1,2)','\t@external','-1+1'])assert.ok(csvCell(value).startsWith('"\''))
  assert.equal(csvCell('Research "A"'),'"Research ""A"""')
  assert.equal(csvCell('Line one\nLine two'),'"Line one\nLine two"')
})
test("regional dates and activity grouping respect the saved timezone at midnight",()=>{
  const stamp="2026-07-20T00:30:00Z"
  assert.equal(workspaceDate(stamp,"America/Los_Angeles"),"2026-07-19")
  assert.equal(workspaceDate(stamp,"Asia/Tokyo"),"2026-07-20")
  const groups=groupActivity([{id:"1",event_type:"profile_updated",occurred_at:stamp}],new Date("2026-07-20T01:00:00Z"),"America/Los_Angeles")
  assert.equal(groups[0].date,"2026-07-19");assert.equal(groups[0].label,"Today")
  const prefs={timezone:"UTC",date_format:"year_month_day",time_format:"24_hour"} as UserPreferences
  assert.match(formatWorkspaceTime(stamp,prefs),/2026-07-20/)
})
test("operations proxy permits exact read exports and validation without broad writes",()=>{
  assert.equal(isAllowedAfdRoute("workspace-export","GET"),true)
  assert.equal(isAllowedAfdRoute("workspace-export","DELETE"),false)
  assert.equal(isAllowedAfdRoute("autonomous-schedule-preview","POST"),true)
  assert.equal(isAllowedAfdRoute("autonomous-schedule-preview/anything","POST"),false)
})
