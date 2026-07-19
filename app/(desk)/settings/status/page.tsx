import { authenticatedBackendClient } from "@/lib/afd/backend"
import { formatEnum } from "@/lib/afd/display"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { StatusPill } from "@/components/desk/status-pill"

type Service = { id: string; label: string; status: string }; type Status = { services: Service[]; last_successful_sync?: string; generated_at: string }
export const metadata = { title: "Connection Status" }
export default async function Page() { let status: Status = { services: [], generated_at: new Date().toISOString() }; try { status = await (await authenticatedBackendClient())<Status>("/api/status") } catch {} const healthy = status.services.filter(service => ["available", "connected"].includes(service.status)).length
return <div className="space-y-7"><SettingsPageHeader title="Connection Status" description="Check user-safe availability for application, market-data, and trading services." />
  <div className="grid gap-4 sm:grid-cols-3"><Summary label="Services reporting" value={String(status.services.length)} /><Summary label="Available" value={String(healthy)} /><Summary label="Needs attention" value={String(Math.max(0, status.services.length - healthy))} /></div>
  <Card><CardHeader><CardTitle>Services</CardTitle><p className="text-sm text-muted-foreground">Internal hosts, processes, and worker identifiers are intentionally hidden.</p></CardHeader><CardContent className="divide-y">{status.services.map(service => <div key={service.id} className="flex items-center justify-between gap-4 py-4"><span className="font-medium">{service.label}</span><StatusPill label={formatEnum(service.status)} tone={["available", "connected"].includes(service.status) ? "good" : service.status === "needs_attention" ? "warn" : "bad"} /></div>)}{!status.services.length && <p className="py-8 text-center text-sm text-muted-foreground">Service status is currently unavailable.</p>}<div className="pt-4 text-xs text-muted-foreground"><p>Last successful sync: {status.last_successful_sync ? new Date(status.last_successful_sync).toLocaleString() : "Not recorded"}</p><p>Snapshot generated: {new Date(status.generated_at).toLocaleString()}</p></div></CardContent></Card>
</div> }
function Summary({ label, value }: { label: string; value: string }) { return <Card><CardContent className="p-5"><p className="text-sm text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></CardContent></Card> }
