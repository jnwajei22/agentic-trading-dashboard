"use client"

import Link from "next/link"
import { usePathname, useRouter } from "next/navigation"
import { useState } from "react"
import { Activity, Bell, Bot, CalendarClock, ChevronDown, Gauge, LineChart, Menu, PanelLeftClose, PanelLeftOpen, Settings, ShieldCheck, SlidersHorizontal, X } from "lucide-react"
import { ThemeToggle } from "@/components/theme-toggle"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import type { Account } from "@/lib/afd/contracts"
import { accountLabel } from "@/lib/afd/display"
import { mutateAfd } from "@/lib/afd/browser-client"
import { StatusPill } from "./status-pill"

const navigation = [["Dashboard", "/dashboard", Gauge], ["Markets", "/markets", LineChart], ["Trade", "/trade", SlidersHorizontal], ["Autonomous", "/autonomous", Bot], ["Schedules", "/schedules", CalendarClock], ["Activity", "/activity", Activity], ["Settings", "/settings", Settings]] as const

function DeskNav({ close, collapsed = false }: { close?: () => void; collapsed?: boolean }) {
  const pathname = usePathname()
  return <nav aria-label="Primary navigation" className="space-y-1">{navigation.map(([label, href, Icon]) => { const active = pathname === href || (href !== "/dashboard" && pathname.startsWith(`${href}/`)); return <Link key={href} href={href} title={collapsed ? label : undefined} aria-label={collapsed ? label : undefined} onClick={close} aria-current={active ? "page" : undefined} className={cn("flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-muted-foreground hover:bg-accent hover:text-foreground focus-visible:ring-2", collapsed && "justify-center px-2", active && "bg-accent text-foreground")}><Icon className="h-4 w-4 shrink-0" />{!collapsed && label}</Link> })}</nav>
}

export function AppShell({ children, user, accounts, backendOnline, brokerReady }: { children: React.ReactNode; user: { name?: string | null; email?: string | null }; accounts: Account[]; backendOnline: boolean; brokerReady: boolean }) {
  const router = useRouter(); const [open, setOpen] = useState(false); const [collapsed, setCollapsed] = useState(false); const [selecting, setSelecting] = useState(false)
  const selected = accounts.find((item) => item.is_default_analysis)
  async function selectAccount(id: string) { setSelecting(true); try { await mutateAfd(`broker/accounts/${encodeURIComponent(id)}/default`, "PUT") } finally { setSelecting(false); router.refresh() } }
  const selector = accounts.length ? <select id="shell-account" disabled={selecting} value={selected?.public_id ?? ""} onChange={(event) => event.target.value && void selectAccount(event.target.value)} className="max-w-80 bg-transparent text-sm font-medium"><option value="" disabled>Select a primary account</option>{accounts.map((account) => <option key={account.public_id} value={account.public_id} disabled={!account.available}>{accountLabel(account)}{account.nickname ? ` · ${account.nickname}` : ""}{account.is_default_analysis ? " · Default" : ""}{!account.available ? " · Unavailable" : ""}</option>)}</select> : <span className="text-sm font-medium">No accounts discovered</span>
  return <div className="min-h-screen bg-background">
    <aside className={cn("fixed inset-y-0 left-0 z-40 hidden border-r bg-card lg:flex lg:flex-col", collapsed ? "w-20" : "w-64")}><Brand collapsed={collapsed} /><div className="flex-1 overflow-y-auto p-4"><DeskNav collapsed={collapsed} /></div>{!collapsed && <div className="border-t p-4 text-xs text-muted-foreground"><div className="mb-2 flex items-center justify-between"><span>Backend</span><StatusPill label={backendOnline ? "Connected" : "Unavailable"} tone={backendOnline ? "good" : "bad"} /></div><div className="flex items-center justify-between"><span>Broker</span><StatusPill label={brokerReady ? "Ready" : "Setup required"} tone={brokerReady ? "good" : "warn"} /></div></div>}<Button variant="ghost" size="icon" className="mb-3 self-center" onClick={() => setCollapsed(!collapsed)} aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}>{collapsed ? <PanelLeftOpen /> : <PanelLeftClose />}</Button></aside>
    {open && <div className="fixed inset-0 z-50 lg:hidden"><button className="absolute inset-0 bg-black/60" aria-label="Close navigation" onClick={() => setOpen(false)} /><aside className="relative h-full w-72 border-r bg-card"><div className="flex items-center justify-between"><Brand /><Button variant="ghost" size="icon" onClick={() => setOpen(false)} aria-label="Close navigation"><X /></Button></div><div className="p-4"><DeskNav close={() => setOpen(false)} /></div></aside></div>}
    <div className={collapsed ? "lg:pl-20" : "lg:pl-64"}><header className="sticky top-0 z-30 flex h-16 items-center gap-3 border-b bg-background/90 px-4 backdrop-blur sm:px-6"><Button className="lg:hidden" variant="ghost" size="icon" onClick={() => setOpen(true)} aria-label="Open navigation"><Menu /></Button><div className="min-w-0 flex-1"><label htmlFor="shell-account" className="block text-xs text-muted-foreground">Trading account</label>{selector}</div>{selected ? <><StatusPill label="DEFAULT" tone="good" /><StatusPill label={selected.environment.toUpperCase()} tone={selected.environment === "live" ? "bad" : "good"} /></> : accounts.length ? <StatusPill label="NO DEFAULT" tone="warn" /> : null}<Button variant="ghost" size="icon" aria-label="Notifications"><Bell className="h-4 w-4" /></Button><ThemeToggle /><div className="hidden border-l pl-4 sm:block"><p className="max-w-44 truncate text-sm font-medium">{user.name ?? user.email ?? "Trader"}</p><Link href="/auth/logout" className="text-xs text-muted-foreground">Log out</Link></div><ChevronDown className="hidden h-4 w-4 text-muted-foreground sm:block" /></header><main className="mx-auto max-w-[1500px] p-4 sm:p-6 lg:p-8">{children}</main></div>
  </div>
}

function Brand({ collapsed = false }: { collapsed?: boolean }) { return <Link href="/dashboard" aria-label="Agentic Trading Desk dashboard" className={cn("flex h-16 items-center gap-3 px-5", collapsed && "justify-center px-2")}><span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-emerald-600 text-white"><ShieldCheck className="h-5 w-5" /></span>{!collapsed && <span><strong className="block text-sm">Agentic Trading Desk</strong><span className="block text-[10px] text-muted-foreground">Trading operations workspace</span></span>}</Link> }
