import type {ExecutionProfileV2} from "@/lib/afd/contracts"

const unhelpful=new Set(["","chatgpt","profile","default","strategy"])
export function profileDisplayName(name:string|undefined,profile:ExecutionProfileV2){
  const candidate=name?.trim()??""
  if(!unhelpful.has(candidate.toLowerCase()))return candidate
  if(profile.trading_policy.preset_id==="hourly_forex")return "Hourly Forex"
  if(profile.market_universe.mode==="groups"&&profile.market_universe.groups.length===1){
    const names:Record<string,string>={forex_majors:"Forex Majors",forex_minors:"Forex Minors",metals:"Metals Focus"}
    if(names[profile.market_universe.groups[0]])return names[profile.market_universe.groups[0]]
  }
  if(profile.market_universe.groups.some(group=>group.startsWith("forex")))return "Balanced Forex"
  return "Default Strategy"
}
