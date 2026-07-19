"use client"

import { useMemo, useState } from "react"
import { useTheme } from "next-themes"
import { Check, Monitor, Moon, Search, Sun } from "lucide-react"
import type { UserPreferences } from "@/lib/afd/contracts"
import { mutateAfd } from "@/lib/afd/browser-client"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { SettingsNotice } from "@/components/settings/settings-page"

const notificationLabels: Record<string, { label: string; detail: string }> = {
  demo_trade_submitted: { label: "Demo trade submitted", detail: "A demo order was successfully submitted." },
  trade_rejected: { label: "Trade rejected", detail: "An order was rejected by validation or the provider." },
  strategy_blocked: { label: "Strategy blocked", detail: "An autonomous run was stopped by a safety rule." },
  daily_loss_limit_reached: { label: "Daily loss limit reached", detail: "Trading was blocked after reaching the configured daily limit." },
  trading_connection_disconnected: { label: "Trading connection disconnected", detail: "A connected provider requires attention." },
  schedule_failed: { label: "Schedule failed", detail: "A scheduled run could not complete." },
  automation_interruption: { label: "Automation interruption", detail: "An autonomous workflow stopped unexpectedly." },
  daily_summary: { label: "Daily summary", detail: "Receive a daily in-app summary when supported." },
}

const commonTimezones = [
  "America/Chicago", "America/New_York", "America/Denver", "America/Los_Angeles", "America/Phoenix",
  "Europe/London", "Europe/Paris", "Europe/Berlin", "Asia/Tokyo", "Asia/Singapore", "Asia/Hong_Kong",
  "Australia/Sydney", "UTC",
]

export function NotificationSettings({ initial }: { initial: UserPreferences }) {
  const [value, setValue] = useState(initial)
  const [status, setStatus] = useState("")
  async function save() {
    setStatus("Saving…")
    try { setValue(await mutateAfd<UserPreferences>("user-preferences", "PATCH", { notifications: value.notifications })); setStatus("Notification preferences saved.") }
    catch { setStatus("Save failed. Your previous preferences remain active.") }
  }
  return <div className="space-y-4">
    <Card><CardHeader><CardTitle>In-app notifications</CardTitle><p className="text-sm text-muted-foreground">Choose which supported events appear in Agentic Trading Desk. Email delivery is not currently configured.</p></CardHeader>
      <CardContent className="divide-y">{Object.entries(notificationLabels).map(([key, item]) => <label className="flex cursor-pointer items-center justify-between gap-4 py-4" key={key}>
        <span><span className="block text-sm font-medium">{item.label}</span><span className="block text-xs text-muted-foreground">{item.detail}</span></span>
        <Switch checked={Boolean(value.notifications[key])} onCheckedChange={checked => setValue(current => ({ ...current, notifications: { ...current.notifications, [key]: checked } }))} />
      </label>)}</CardContent>
    </Card>
    {status && <SettingsNotice message={status} tone={status.includes("saved") ? "good" : status.includes("failed") ? "bad" : "neutral"} />}
    <div className="flex justify-end"><Button onClick={() => void save()}>Save notification preferences</Button></div>
  </div>
}

export function AppearanceSettings({ initial }: { initial: UserPreferences }) {
  const { setTheme } = useTheme(); const [appearance, setAppearance] = useState(initial.appearance); const [status, setStatus] = useState("")
  const options = [{ value: "light", label: "Light", detail: "Always use the light theme.", Icon: Sun }, { value: "dark", label: "Dark", detail: "Always use the dark theme.", Icon: Moon }, { value: "system", label: "System", detail: "Match your device setting.", Icon: Monitor }] as const
  async function save() { setStatus("Saving…"); try { await mutateAfd("user-preferences", "PATCH", { appearance }); setTheme(appearance); setStatus("Appearance saved.") } catch { setStatus("Save failed. Your previous appearance remains active.") } }
  return <div className="space-y-4"><div className="grid gap-3 md:grid-cols-3">{options.map(({ value, label, detail, Icon }) => <button key={value} onClick={() => setAppearance(value)} className={`rounded-xl border p-5 text-left transition-colors ${appearance === value ? "border-emerald-500 bg-emerald-500/10" : "hover:bg-accent"}`}>
    <div className="flex items-center justify-between"><Icon className="h-5 w-5" />{appearance === value && <Check className="h-5 w-5 text-emerald-500" />}</div><h2 className="mt-4 font-semibold">{label}</h2><p className="mt-1 text-sm text-muted-foreground">{detail}</p>
  </button>)}</div>{status && <SettingsNotice message={status} tone={status.includes("saved") ? "good" : status.includes("failed") ? "bad" : "neutral"} />}<div className="flex justify-end"><Button onClick={() => void save()}>Save appearance</Button></div></div>
}

export function RegionalSettings({ initial }: { initial: UserPreferences }) {
  const [value, setValue] = useState(initial); const [status, setStatus] = useState(""); const [timezoneQuery, setTimezoneQuery] = useState("")
  const browserTimezone = useMemo(() => Intl.DateTimeFormat().resolvedOptions().timeZone, [])
  const timezones = useMemo(() => {
    const supportedValuesOf = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf
    const supported = supportedValuesOf ? supportedValuesOf("timeZone") : commonTimezones
    const all = [...new Set([value.timezone, browserTimezone, ...commonTimezones, ...supported])]
    return all.filter(item => item.toLowerCase().includes(timezoneQuery.toLowerCase())).slice(0, 80)
  }, [browserTimezone, timezoneQuery, value.timezone])
  async function save() { setStatus("Saving…"); try { setValue(await mutateAfd<UserPreferences>("user-preferences", "PATCH", value)); setStatus("Regional settings saved.") } catch { setStatus("Save failed. Your previous regional settings remain active.") } }
  return <div className="space-y-4"><Card><CardHeader><CardTitle>Timezone</CardTitle><p className="text-sm text-muted-foreground">Used for schedules and date previews throughout the application.</p></CardHeader><CardContent className="space-y-3">
    <div className="relative"><Search className="absolute left-3 top-3 h-4 w-4 text-muted-foreground" /><Input value={timezoneQuery} onChange={event => setTimezoneQuery(event.target.value)} placeholder="Search timezones" className="pl-9" /></div>
    <select value={value.timezone} onChange={event => setValue(current => ({ ...current, timezone: event.target.value }))} className="h-10 w-full rounded border bg-background px-3" aria-label="Timezone">{timezones.map(item => <option value={item} key={item}>{item}</option>)}</select>
    <div className="flex flex-wrap gap-2"><Button type="button" size="sm" variant="outline" onClick={() => setValue(current => ({ ...current, timezone: browserTimezone }))}>Use device timezone</Button><span className="self-center text-xs text-muted-foreground">Detected: {browserTimezone}</span></div>
  </CardContent></Card>
  <Card><CardHeader><CardTitle>Display formats</CardTitle><p className="text-sm text-muted-foreground">These settings change presentation only and do not affect execution calculations.</p></CardHeader><CardContent className="grid gap-4 sm:grid-cols-3">
    <Choice label="Date format" value={value.date_format} values={["locale", "month_day_year", "day_month_year", "year_month_day"]} onChange={date_format => setValue(current => ({ ...current, date_format }))} />
    <Choice label="Time format" value={value.time_format} values={["locale", "12_hour", "24_hour"]} onChange={time_format => setValue(current => ({ ...current, time_format }))} />
    <Choice label="Currency display" value={value.currency_display} values={["account", "usd"]} onChange={currency_display => setValue(current => ({ ...current, currency_display }))} />
  </CardContent></Card>
  {status && <SettingsNotice message={status} tone={status.includes("saved") ? "good" : status.includes("failed") ? "bad" : "neutral"} />}<div className="flex justify-end"><Button onClick={() => void save()}>Save regional settings</Button></div></div>
}

function Choice({ label, value, values, onChange }: { label: string; value: string; values: string[]; onChange: (value: string) => void }) { return <div><Label>{label}</Label><select value={value} onChange={event => onChange(event.target.value)} className="mt-1 h-10 w-full rounded border bg-background px-3">{values.map(item => <option value={item} key={item}>{item.replaceAll("_", " ").replace(/\b\w/g, letter => letter.toUpperCase())}</option>)}</select></div> }
