"use client"

import Link from "next/link"
import {useEffect,useState} from "react"
import {Bell} from "lucide-react"
import {browserAfdFetch} from "@/lib/afd/browser-client"
import type {ActivityEvent} from "@/lib/afd/contracts"
import {formatEnum} from "@/lib/afd/display"
import {Button} from "@/components/ui/button"
import {Popover,PopoverContent,PopoverTrigger} from "@/components/ui/popover"
import {usePreferences,formatWorkspaceTime} from "./preferences-provider"

export function notificationCategory(event:ActivityEvent){
  const type=event.event_type;const outcome=(event.outcome??"").toLowerCase();const reasons=(event.reason_codes??[]).join(" ")
  if(/daily.*loss/.test(reasons))return "daily_loss_limit_reached"
  if(type==="schedule_run"&&/error|fail|exhaust/.test(outcome))return "schedule_failed"
  if(type==="order_rejected")return "trade_rejected"
  if(type==="demo_order_submitted"&&/verified|submitted/.test(outcome))return "demo_trade_submitted"
  if(type==="strategy_evaluated"&&outcome==="blocked")return "strategy_blocked"
  if(type.includes("connection")&&/disabled|removed|disconnect/.test(type))return "trading_connection_disconnected"
  if(type==="strategy_evaluated"&&outcome==="error")return "automation_interruption"
  return null
}
export function NotificationInbox(){
  const {preferences,ready}=usePreferences();const [events,setEvents]=useState<ActivityEvent[]>([]);const [error,setError]=useState(false);const [read,setRead]=useState("");const [open,setOpen]=useState(false)
  useEffect(()=>{setRead(localStorage.getItem("agentic.notifications.read")??"");let cancelled=false
    async function load(){if(document.visibilityState!=="visible")return;try{const data=await browserAfdFetch<{events:ActivityEvent[]}>("activity");if(!cancelled){setEvents(data.events??[]);setError(false)}}catch{if(!cancelled)setError(true)}}
    void load();const timer=setInterval(()=>void load(),30_000);return()=>{cancelled=true;clearInterval(timer)}},[])
  const visible=events.filter(event=>{const category=notificationCategory(event);return category&&preferences.notifications[category]}).slice(0,30)
  const unread=visible.filter(event=>event.occurred_at>read).length
  function markRead(){const latest=visible[0]?.occurred_at??new Date().toISOString();setRead(latest);localStorage.setItem("agentic.notifications.read",latest)}
  return <Popover open={open} onOpenChange={setOpen}><PopoverTrigger asChild><Button variant="ghost" size="icon" className="relative rounded-full" aria-label={`Notifications${unread?`, ${unread} unread`:""}`}><Bell className="h-4 w-4"/>{unread>0&&<span className="absolute right-1 top-1 h-2 w-2 rounded-full bg-emerald-500"/>}</Button></PopoverTrigger><PopoverContent align="end" className="w-[min(380px,90vw)] rounded-2xl p-0"><div className="flex items-center justify-between border-b px-4 py-3"><h2 className="font-semibold">Notifications</h2><Button size="sm" variant="ghost" onClick={markRead} disabled={!unread}>Mark read</Button></div><div className="max-h-80 overflow-y-auto">{error&&<p role="status" className="p-4 text-xs text-muted-foreground">Updates unavailable. We’ll retry shortly.</p>}{!ready?<p className="p-4 text-sm">Notification preferences unavailable.</p>:!visible.length?<p className="p-5 text-sm text-muted-foreground">No matching notifications. Your enabled alerts will appear here.</p>:visible.map(event=><Link href="/activity" onClick={()=>setOpen(false)} key={event.id} className="block border-b px-4 py-3 hover:bg-muted/50"><p className="text-sm font-medium">{formatEnum(event.event_type)}{event.occurred_at>read&&<span className="ml-2 text-emerald-500">•</span>}</p><p className="mt-1 text-xs text-muted-foreground">{event.summary||(event.reason_codes??[]).map(formatEnum).join(", ")||formatEnum(event.outcome)}</p><time className="mt-1 block text-[11px] text-muted-foreground">{formatWorkspaceTime(event.occurred_at,preferences)}</time></Link>)}</div><div className="flex justify-between px-4 py-3 text-xs"><Link href="/activity" onClick={()=>setOpen(false)}>All activity</Link><Link href="/settings/notifications" onClick={()=>setOpen(false)}>Preferences</Link></div></PopoverContent></Popover>
}
