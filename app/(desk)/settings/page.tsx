import Link from "next/link"
import { ArrowRight, Bell, Bot, Clock, Eye, HeartPulse, Lock, Palette, ShieldCheck, UserRoundCog } from "lucide-react"
import { PageHeader } from "@/components/desk/page-header"
import { Card, CardContent } from "@/components/ui/card"

const groups = [
  { title: "Trading", description: "Connections, accounts, and integrations used by the trading workspace.", items: [
    ["/settings/broker", ShieldCheck, "Trading Connections", "Connect, verify, refresh, or remove trading providers."],
    ["/settings/accounts", UserRoundCog, "Trading Accounts", "Manage primary account selection, nicknames, and availability."],
    ["/settings/integrations/mcp", Bot, "ChatGPT & MCP", "Connect ChatGPT and manage authorized AI applications."],
  ]},
  { title: "Preferences", description: "Personalize notifications, appearance, timezone, and display formats.", items: [
    ["/settings/notifications", Bell, "Notifications", "Choose which supported events appear in the application."],
    ["/settings/appearance", Palette, "Appearance", "Choose light, dark, or system appearance."],
    ["/settings/regional", Clock, "Regional Settings", "Set timezone, date, time, and currency display preferences."],
  ]},
  { title: "Security and system", description: "Review access, privacy, and service availability.", items: [
    ["/settings/security", Lock, "Security", "Review the active session and connected trading platforms."],
    ["/settings/privacy", Eye, "Data and Privacy", "Understand stored data and available export or removal workflows."],
    ["/settings/status", HeartPulse, "Connection Status", "Check user-safe availability for application services."],
  ]},
] as const

export const metadata = { title: "Settings" }
export default function SettingsPage() {
  return <div className="space-y-8">
    <PageHeader eyebrow="Settings" title="Workspace settings" description="Manage trading connections, preferences, security, and integrations from one place." />
    <div className="space-y-8">{groups.map(group => <section key={group.title} className="space-y-3">
      <div><h2 className="text-lg font-semibold">{group.title}</h2><p className="text-sm text-muted-foreground">{group.description}</p></div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">{group.items.map(([href, Icon, title, detail]) => <Link href={href} key={href} className="group">
        <Card className="h-full transition-colors hover:bg-accent/60"><CardContent className="flex h-full items-start gap-4 p-5">
          <div className="rounded-lg border bg-background p-2"><Icon className="h-5 w-5" /></div>
          <div className="min-w-0 flex-1"><h3 className="font-semibold">{title}</h3><p className="mt-1 text-sm text-muted-foreground">{detail}</p></div>
          <ArrowRight className="mt-1 h-4 w-4 text-muted-foreground transition-transform group-hover:translate-x-1" />
        </CardContent></Card>
      </Link>)}</div>
    </section>)}</div>
  </div>
}
