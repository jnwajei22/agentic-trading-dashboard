import type {UserPreferences} from "./contracts"

export function formatWorkspaceTime(value:string,preferences:UserPreferences,kind:"time"|"date"|"both"="both"){
  const date=new Date(value);if(!Number.isFinite(date.getTime()))return "Unavailable"
  const locale=preferences.date_format==="month_day_year"?"en-US":preferences.date_format==="day_month_year"?"en-GB":preferences.date_format==="year_month_day"?"en-CA":undefined
  const options:Intl.DateTimeFormatOptions={timeZone:preferences.timezone}
  if(kind!=="time"){options.year="numeric";options.month=preferences.date_format==="locale"?"short":"2-digit";options.day="numeric"}
  if(kind!=="date"){options.hour="numeric";options.minute="2-digit";if(preferences.time_format!=="locale")options.hour12=preferences.time_format==="12_hour"}
  return new Intl.DateTimeFormat(locale,options).format(date)
}
export function workspaceDate(value:string|Date,timezone:string){return new Intl.DateTimeFormat("en-CA",{timeZone:timezone,year:"numeric",month:"2-digit",day:"2-digit"}).format(new Date(value))}
