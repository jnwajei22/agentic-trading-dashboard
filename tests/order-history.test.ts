import assert from "node:assert/strict"
import test from "node:test"
import {activityNumber, eventTime, historyRows, statusGroup, type BrokerRow} from "../app/(desk)/trade/activity-model"

const row = (id: string, time: string | number | null, status = "Filled"): BrokerRow => ({id, time, status, symbol:"EURUSD", side:"buy", type:"market", quantity:.01, entry:1.11394, pnl:null, stop_loss:null, take_profit:null})

test("history normalizes broker seconds, milliseconds, and ISO dates", () => {
  const milliseconds = Date.parse("2026-10-29T07:00:00Z")
  assert.equal(eventTime(milliseconds / 1000), milliseconds)
  assert.equal(eventTime(String(milliseconds)), milliseconds)
  assert.equal(eventTime("2026-10-29T07:00:00Z"), milliseconds)
  assert.equal(eventTime(null), null)
  assert.equal(eventTime("nonsense"), null)
})
test("history filters statuses and searches IDs without losing cancelled protective orders", () => {
  const rows = [row("entry",1),row("stop",2,"Canceled"),row("target",3,"Expired")]
  assert.deepEqual(historyRows(rows,"","all").map(r=>r.id),["target","stop","entry"])
  assert.deepEqual(historyRows(rows,"","cancelled").map(r=>r.id),["target","stop"])
  assert.deepEqual(historyRows(rows,"STOP","all").map(r=>r.id),["stop"])
  assert.equal(statusGroup("Partially Filled"),"other")
})
test("missing dates keep broker order stable; numeric formatting preserves zero and hides float noise", () => {
  assert.deepEqual(historyRows([row("a",null),row("b",null)],"","all").map(r=>r.id),["a","b"])
  assert.equal(activityNumber(0),"0")
  assert.equal(activityNumber(20.000000000000018,1),"20")
  assert.equal(activityNumber(null),"—")
  assert.equal(activityNumber(Number.NaN),"—")
})
