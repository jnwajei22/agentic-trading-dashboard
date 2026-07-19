import type {ProfileSummary} from "@/lib/afd/contracts"
import {formatEnum} from "@/lib/afd/display"

export const DAYS=["monday","tuesday","wednesday","thursday","friday","saturday","sunday"] as const
export const WEEKDAYS=DAYS.slice(0,5)
export type DayPreset="weekdays"|"every_day"|"custom"
export type WeekendCapability="weekday_only"|"crypto_only"|"mixed"|"unknown"
export type ScheduleBuilder={type:string;timezone:string;days:string[];sessions:string[];start:string;end:string;interval:number;time:string;marketAware:boolean;lateness:number;dayPreset:DayPreset;daysCustomized:boolean}

export function weekendCapability(profile?:ProfileSummary):WeekendCapability{
  const configuration=profile?.profile_v2
  if(!configuration)return "unknown"
  const groups=configuration.market_universe?.groups??[]
  const hasCrypto=groups.includes("crypto")
  const hasWeekday=groups.some(group=>group!=="crypto")
  if(hasCrypto&&hasWeekday)return "mixed"
  if(hasCrypto)return "crypto_only"
  if(groups.length||["forex","equities","options"].includes(configuration.asset_class??""))return "weekday_only"
  return "unknown"
}

export function presetForDays(days:string[]):DayPreset{
  if(sameDays(days,WEEKDAYS))return "weekdays"
  if(sameDays(days,DAYS))return "every_day"
  return "custom"
}

export function daysForPreset(preset:Exclude<DayPreset,"custom">){return preset==="weekdays"?[...WEEKDAYS]:[...DAYS]}
export function defaultPreset(capability:WeekendCapability,type:string):Exclude<DayPreset,"custom">{return type==="market_session"||capability!=="crypto_only"?"weekdays":"every_day"}
export function weekendAllowed(capability:WeekendCapability){return capability==="crypto_only"||capability==="mixed"}

export function naturalSchedule(value:ScheduleBuilder,capability:WeekendCapability){
  const days=dayPhrase(value.days)
  const marketAware=value.marketAware?", while selected markets are open":""
  const mixed=value.days.some(day=>day==="saturday"||day==="sunday")&&capability==="mixed"?"; only markets available on weekends will be evaluated on Saturday and Sunday":""
  if(value.type==="market_session")return `Runs during the ${value.sessions.map(formatEnum).join(", ")} session, ${days}, in ${value.timezone}${marketAware}${mixed}.`
  if(value.days.length===7&&capability==="crypto_only")return "Runs every day for available crypto markets."
  if(value.type==="recurring_interval")return `Runs every ${value.interval<60?`${value.interval} minutes`:`${value.interval/60} hours`}, ${days}, between ${value.start} and ${value.end} in ${value.timezone}${marketAware}${mixed}.`
  return `Runs at ${value.time}, ${days}, in ${value.timezone}${marketAware}${mixed}.`
}

export function dayPhrase(days:string[]){
  if(sameDays(days,WEEKDAYS))return "Monday through Friday"
  if(sameDays(days,DAYS))return "every day"
  const labels=days.map(formatEnum)
  if(labels.length<2)return labels[0]??"no trading days"
  if(labels.length===2)return `${labels[0]} and ${labels[1]}`
  return `${labels.slice(0,-1).join(", ")}, and ${labels.at(-1)}`
}

function sameDays(left:readonly string[],right:readonly string[]){return left.length===right.length&&right.every(day=>left.includes(day))}
