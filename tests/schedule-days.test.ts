import assert from "node:assert/strict"
import {readFileSync} from "node:fs"
import test from "node:test"
import type {ProfileSummary} from "../lib/afd/contracts"
import {DAYS,WEEKDAYS,daysForPreset,defaultPreset,naturalSchedule,presetForDays,weekendCapability,type ScheduleBuilder} from "../app/(desk)/schedules/schedule-days"

const manager=readFileSync(new URL("../app/(desk)/schedules/schedule-manager.tsx",import.meta.url),"utf8")
const profile=(groups:string[],assetClass?:string)=>({profile_v2:{asset_class:assetClass,market_universe:{mode:"groups",groups}}} as unknown as ProfileSummary)
const builder=(days:string[],type="market_session"):ScheduleBuilder=>({type,timezone:"America/Chicago",days,sessions:["london"],start:"07:00",end:"15:00",interval:120,time:"09:00",marketAware:true,lateness:600,dayPreset:presetForDays(days),daysCustomized:false})

test("Weekdays and Every Day presets select the canonical recurrence days",()=>{
  assert.deepEqual(daysForPreset("weekdays"),WEEKDAYS)
  assert.deepEqual(daysForPreset("every_day"),DAYS)
})
test("Custom mode reveals compact individually toggleable day buttons",()=>{
  assert.match(manager,/dayPreset==="custom"&&/);assert.match(manager,/DAYS\.map/)
  assert.match(manager,/builder\.days\.filter/);assert.match(manager,/\[\.\.\.builder\.days,day\]/)
  assert.match(manager,/grid-cols-4.*sm:grid-cols-7/);assert.doesNotMatch(manager,/grid-cols-7[^\n]*overflow-x/)
})
test("weekend capability follows selected strategy assets and fails closed",()=>{
  assert.equal(weekendCapability(profile(["forex_majors"])),"weekday_only")
  assert.equal(weekendCapability(profile(["crypto"])),"crypto_only")
  assert.equal(weekendCapability(profile(["crypto","forex_majors"])),"mixed")
  assert.equal(weekendCapability(undefined),"unknown")
  assert.match(manager,/weekend&&!allowed/);assert.match(manager,/No selected markets are normally tradable on this day/)
})
test("crypto and mixed strategies permit weekends with mixed-market explanation",()=>{
  assert.match(manager,/capability==="mixed"/);assert.match(manager,/Only markets available on weekends will be evaluated/)
  assert.match(manager,/weekendAllowed\(capability\)/)
})
test("Market Session defaults to compact Weekdays with a Customize action",()=>{
  assert.equal(defaultPreset("crypto_only","market_session"),"weekdays")
  assert.match(manager,/Weekdays<\/strong> · Monday through Friday/);assert.match(manager,/>Customize<\/Button>/)
  assert.match(manager,/type==="market_session".*dayPreset:"weekdays"/)
})
test("validation requires at least one trading day",()=>{
  assert.match(manager,/if\(!builder\.days\.length\)/);assert.match(manager,/Select at least one trading day\./)
})
test("natural-language preview reflects presets custom days sessions and weekend scope",()=>{
  assert.equal(naturalSchedule(builder([...WEEKDAYS]),"weekday_only"),"Runs during the London session, Monday through Friday, in America/Chicago, while selected markets are open.")
  assert.equal(naturalSchedule(builder(["monday","wednesday","friday"]),"weekday_only"),"Runs during the London session, Monday, Wednesday, and Friday, in America/Chicago, while selected markets are open.")
  assert.equal(naturalSchedule(builder([...DAYS],"daily"),"crypto_only"),"Runs every day for available crypto markets.")
  assert.match(naturalSchedule(builder([...DAYS]),"mixed"),/only markets available on weekends/)
})
test("legacy weekend selections remain intact and visibly warn",()=>{
  const saved=[...WEEKDAYS,"saturday"]
  assert.equal(presetForDays(saved),"custom");assert.deepEqual(saved,[...WEEKDAYS,"saturday"])
  assert.match(manager,/legacyWeekend/);assert.match(manager,/It is preserved until you deliberately choose a new preset/)
})
