import assert from "node:assert/strict"
import {readFileSync} from "node:fs"
import test from "node:test"
import {activeCalculation,entryForSide,previewExpired,protectionMessage,stepQuantity} from "../app/(desk)/trade/ticket-model"

const source=readFileSync(new URL("../app/(desk)/trade/workspace.tsx",import.meta.url),"utf8")
const quote={bid:1.3452,ask:1.34528,source:"TradeLocker",stale:false}

test("Trade ticket uses accessible Buy and Sell controls without a Direction field",()=>{
  assert.doesNotMatch(source,/>Direction<|label="Direction"/)
  assert.match(source,/"buy","sell"/);assert.match(source,/aria-pressed/);assert.match(source,/selected<\/span>/)
})
test("Buy uses Ask and Sell uses Bid",()=>{
  assert.equal(entryForSide("buy",quote),1.34528);assert.equal(entryForSide("sell",quote),1.3452)
})
test("side-specific stop and target validation updates immediately",()=>{
  assert.match(protectionMessage("Stop Loss","buy",1.35,1.34528)!,/below.*Buy/)
  assert.match(protectionMessage("Stop Loss","sell",1.34,1.3452)!,/above.*Sell/)
  assert.match(protectionMessage("Take Profit","buy",1.34,1.34528)!,/above.*Buy/)
  assert.match(protectionMessage("Take Profit","sell",1.35,1.3452)!,/below.*Sell/)
})
test("quantity controls honor provider step and bounds and show units",()=>{
  assert.equal(stepQuantity(.5,1,.1,.1,1),.6);assert.equal(stepQuantity(.5,-1,.1,.5,1),.5)
  assert.match(source,/Decrease quantity/);assert.match(source,/Increase quantity/);assert.match(source,/Lots/)
})
test("stop and target modes preserve entered values while derived prices come from calculations",()=>{
  for(const token of ['["price","pips"]','["price","pips","reward_multiple"]',"Derived price","Define a valid Stop Loss before using an R:R target"])assert.match(source,new RegExp(token.replaceAll("[","\\[").replaceAll("]","\\]")))
  assert.match(source,/value:\s*event\.target\.value/)
})
test("review is concise and meaningful values are conditional",()=>{
  assert.match(source,/Select a symbol, side, and quantity to calculate the order/)
  assert.doesNotMatch(source,/value="Unavailable"/)
  assert.match(source,/calculation\.pip_value&&/);assert.match(source,/margin_estimate!=null/)
})
test("stale calculation responses are ignored and polling is cancellable",()=>{
  assert.match(source,/current===sequence\.current/);assert.match(source,/AbortController/);assert.match(source,/controller\.abort/)
  assert.match(source,/document\.hidden/);assert.match(source,/setTimeout\(run,5000\)/)
})
test("preview values freeze and expiry requires a refresh",()=>{
  const live={price:1};const frozen={price:2};const preview={calculation:frozen,expires_at:new Date(Date.now()+1000).toISOString()}
  assert.equal(activeCalculation(live,preview),frozen);assert.equal(previewExpired(preview),false)
  assert.equal(previewExpired({...preview,expires_at:new Date(Date.now()-1).toISOString()}),true)
  assert.match(source,/if\(!payload\.account_id\|\|ticket\.preview\)return/)
})
test("executable quotes come only from the same-origin backend contract",()=>{
  assert.match(source,/trading\/order-calculations/);assert.match(source,/quote\.source/)
  assert.doesNotMatch(source,/TradingView|s\.tradingview|widgetembed/)
})
