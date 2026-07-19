import assert from "node:assert/strict"
import {readFileSync} from "node:fs"
import test from "node:test"
import {normalizeSourceProviders} from "../lib/afd/source-attribution"

const source=(path:string)=>readFileSync(new URL(`../${path}`,import.meta.url),"utf8")

test("upcoming schedules resolve profile names and stack the next run",()=>{
  const dashboard=source("app/(desk)/dashboard/page.tsx")
  assert.match(dashboard,/data\.profiles\.find/)
  assert.match(dashboard,/item\.public_id === schedule\.profile_ref/)
  assert.match(dashboard,/profile\?\.name \?\? "Unnamed execution profile"/)
  assert.match(dashboard,/className="block rounded border p-3"/)
  assert.doesNotMatch(dashboard,/>\{schedule\.profile_ref\}</)
})

test("known and delimited market sources receive canonical provider names",()=>{
  assert.equal(normalizeSourceProviders("finnhub, fred"),"Finnhub · FRED")
  assert.equal(normalizeSourceProviders("tradingview / tradelocker"),"TradingView · TradeLocker")
  assert.equal(normalizeSourceProviders("Custom Provider / fred"),"Custom Provider · FRED")
})

test("all Markets provider attribution is rendered through Source",()=>{
  const markets=source("app/(desk)/markets/workspace.tsx")
  assert.match(markets,/normalizeSourceProviders\(label\)/)
  assert.match(markets,/Source: \{providers\}/)
  assert.match(markets,/item\.source_name\?\?"finnhub"/)
  assert.match(markets,/label=\{driver\.source\}/)
  assert.match(markets,/label=\{item\.source\}/)
  assert.match(markets,/label=\{series\.source\}/)
  assert.doesNotMatch(markets,/\{driver\.source\}<\/p>|\{series\.source\} ·/)
})
