import Link from "next/link"
import { KeyRound, LogOut, ShieldCheck } from "lucide-react"
import { authenticatedBackendClient } from "@/lib/afd/backend"
import { requireSession } from "@/lib/afd/session"
import type { Connection } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { StatusPill } from "@/components/desk/status-pill"

export const metadata = { title: "Security" }
export default async function Page() { const session = await requireSession("/settings/security"); let connections: Connection[] = []; try { connections = (await (await authenticatedBackendClient())<{ connections: Connection[] }>("/api/broker/connections")).connections ?? [] } catch {} const verified = connections.map(item => item.last_verified_at).filter(Boolean).sort().at(-1)
return <div className="space-y-7"><SettingsPageHeader title="Security" description="Review the active session and the trading platforms authorized for this account." />
  <div className="grid gap-4 lg:grid-cols-2">
    <Card><CardHeader><div className="flex items-center justify-between"><CardTitle className="flex items-center gap-2"><ShieldCheck className="h-5 w-5" />Current session</CardTitle><StatusPill label="Active" tone="good" /></div></CardHeader><CardContent className="space-y-4 text-sm"><Datum label="Authenticated email" value={session.user.email ?? "Unavailable"} /><Datum label="Session location" value="This browser" /><Button asChild variant="outline"><Link href="/auth/logout"><LogOut className="mr-2 h-4 w-4" />Sign out</Link></Button></CardContent></Card>
    <Card><CardHeader><div className="flex items-center justify-between"><CardTitle className="flex items-center gap-2"><KeyRound className="h-5 w-5" />Trading access</CardTitle><StatusPill label={connections.length ? "Connected" : "Not connected"} tone={connections.length ? "good" : "neutral"} /></div></CardHeader><CardContent className="space-y-4 text-sm"><Datum label="Connected platforms" value={connections.length ? [...new Set(connections.map(item => item.platform_name || "TradeLocker"))].join(", ") : "None"} /><Datum label="Last verified" value={verified ? new Date(verified).toLocaleString() : "Not recorded"} /><Button asChild variant="outline"><Link href="/settings/broker">Manage trading connections</Link></Button></CardContent></Card>
  </div>
</div> }
function Datum({ label, value }: { label: string; value: string }) { return <div className="flex items-start justify-between gap-4 border-b pb-3 last:border-0 last:pb-0"><span className="text-muted-foreground">{label}</span><strong className="text-right font-medium">{value}</strong></div> }
