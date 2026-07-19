import assert from "node:assert/strict"
import {readFileSync} from "node:fs"
import test from "node:test"
import type {ExecutionProfileV2} from "../lib/afd/contracts"
import {buildProfilePatch} from "../lib/afd/profile"
import {profileDisplayName} from "../app/(desk)/autonomous/[profileId]/profile-display"

const source=(path:string)=>readFileSync(new URL(`../${path}`,import.meta.url),"utf8")
const editor=source("app/(desk)/autonomous/[profileId]/profile-editor.tsx")
const page=source("app/(desk)/autonomous/[profileId]/page.tsx")
const profile={trading_policy:{preset_id:"hourly_forex"},market_universe:{mode:"groups",groups:["forex_majors"]}} as unknown as ExecutionProfileV2

test("customer editor omits implementation format terminology",()=>{
  for(const text of ["Execution Profile V2","Schema V2","Native V2","Schema v"])assert.doesNotMatch(editor,new RegExp(`[\"'>]${text}`,"i"))
  assert.doesNotMatch(page,/Execution Profile V2|Schema V2|Native V2/)
})
test("actual profile name heads the page and legacy names receive useful display fallbacks",()=>{
  assert.equal(profileDisplayName("London Session",profile),"London Session")
  assert.equal(profileDisplayName("ChatGPT",profile),"Hourly Forex")
  assert.match(page,/listed\?\.name/);assert.match(editor,/<h1[^>]*>\{title\}/)
  assert.match(editor,/contract\.account_number/);assert.doesNotMatch(page,/title=\{`Profile/)
})
test("header retains synchronized unsaved state and prominent enabled control",()=>{
  assert.match(editor,/dirty&&<Badge[^>]*>Unsaved Changes/)
  assert.match(editor,/aria-label="Strategy Enabled"/);assert.match(editor,/>Strategy Enabled</)
})
test("market selection progressively renders only the active method",()=>{
  assert.match(editor,/method==="all_available"&&/);assert.match(editor,/method==="groups"&&<GroupSelect/)
  assert.match(editor,/method==="custom"&&<InstrumentSelect/)
  assert.match(editor,/All markets supported by this trading account may be considered/)
  assert.doesNotMatch(editor,/freeform|Custom symbol/i)
})
test("custom market search is API-backed keyboard accessible stale-safe and duplicate-safe",()=>{
  assert.match(editor,/accounts\/\$\{encodeURIComponent\(accountAlias\)\}\/instruments/)
  for(const token of ["ArrowDown","ArrowUp","Enter","Escape","role=\"combobox\"","role=\"listbox\"","No matching markets"])assert.match(editor,new RegExp(token))
  assert.ok(editor.includes('aria-label={`Remove ${'))
  assert.match(editor,/current===sequence\.current/);assert.match(editor,/AbortController/)
  assert.match(editor,/!value\.includes\(item\.instrument_id\)/)
})
test("Basic mode uses compact customer-oriented groups",()=>{
  for(const heading of ["Trading Rules","Risk","Exit Strategy"])assert.match(editor,new RegExp(`title=\"${heading}\"`))
  assert.match(editor,/lg:grid-cols-3/);assert.match(editor,/unit="%"/);assert.match(editor,/unit="Positions"/);assert.match(editor,/unit="R"/)
})
test("Advanced mode uses one-level collapsible sections with summaries and error markers",()=>{
  for(const heading of ["Risk Limits","Exposure and Capital","Daily Protection","Trade Management"])assert.match(editor,new RegExp(`title=\"${heading}\"`))
  assert.match(editor,/Accordion type="multiple"/);assert.match(editor,/Needs Attention/);assert.match(editor,/setOpenSections/)
  assert.match(editor,/profile\.risk_policy\.base_risk_pct.*base/);assert.match(editor,/Trailing Stop.*Break-Even/)
})
test("conditional controls preserve shared state rather than clearing hidden values",()=>{
  assert.match(editor,/trailing_stop\.enabled&&/);assert.match(editor,/break_even\.enabled&&/)
  assert.match(editor,/risk_policy\.mode==="fixed"\?/);assert.match(editor,/take_profit\.mode==="reward_to_risk"&&/)
  assert.match(editor,/setProfile\(current=>setPath\(current,path,value\)\)/)
  assert.doesNotMatch(editor,/trailing_stop\.enabled[^\n]*update\("exit_policy\.trailing_stop\.activation_value",null\)/)
})
test("profile PATCH preserves hidden schedule data and unchanged top-level sections",()=>{
  const full={...profile,schedule_policy:{timezone:"UTC",times:["10:00"]},enabled:true} as unknown as ExecutionProfileV2
  const changed=structuredClone(full);changed.enabled=false
  assert.deepEqual(buildProfilePatch(full,changed),{enabled:false})
  assert.equal("schedule_policy" in buildProfilePatch(full,{...changed,schedule_policy:{timezone:"CST",times:[]}} as unknown as ExecutionProfileV2),false)
})
test("save bar covers all states confirms discard and warns on navigation",()=>{
  for(const state of ["No Unsaved Changes","Unsaved Changes","Saving…","Saved","Save Failed"])assert.match(editor,new RegExp(state))
  assert.match(editor,/Discard unsaved changes/);assert.match(editor,/beforeunload/);assert.match(editor,/window\.confirm/)
  assert.match(editor,/pb-28/);assert.match(editor,/flex-col.*sm:flex-row/);assert.doesNotMatch(editor,/overflow-x-auto/)
})
