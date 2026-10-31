import {WorkspaceTime} from "@/components/desk/workspace-time"
import { workspaceBackendClient } from "@/lib/afd/backend"
import { formatEnum } from "@/lib/afd/display"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { StatusPill } from "@/components/desk/status-pill"

type Service = { id: string; label: string; status: string; message?: string }; type Status = { services: Service[]; last_successful_sync?: string; generated_at: string }
export const metadata = { title: "Connection Status" }
export default async function Page() { let status: Status = { services: [], generated_at: new Date().toISOString() }; try { status = await (await workspaceBackendClient())<Status>("/api/status") } catch(error) { throw error } const healthy = status.services.filter(service => ["available", "connected", "configured"].includes(service.status)).length
return <div className="space-y-7"><SettingsPageHeader title="Connection Status" description="Check user-safe availability for application, market-data, and trading services." />
  <div className="grid gap-4 sm:grid-cols-3"><Summary label="Services reporting" value={String(status.services.length)} /><Summary label="Available / configured" value={String(healthy)} /><Summary label="Needs attention" value={String(status.services.filter(service=>["needs_attention","unavailable","not_running"].includes(service.status)).length)} /></div>
  <Card><CardHeader><CardTitle>Services</CardTitle><p className="text-sm text-muted-foreground">Configuration and observed availability are shown separately. A configured provider may still be temporarily unavailable.</p></CardHeader><CardContent className="divide-y">{status.services.map(service => <div key={service.id} className="flex items-center justify-between gap-4 py-4"><div><span className="text-sm font-medium">{service.label}</span>{service.message&&<p className="mt-1 max-w-xl text-xs leading-relaxed text-muted-foreground">{service.message}</p>}</div><StatusPill label={formatEnum(service.status)} tone={["available", "connected", "configured"].includes(service.status) ? "good" : ["needs_attention","not_running"].includes(service.status) ? "warn" : service.status === "not_configured" ? "neutral" : "bad"} /></div>)}{!status.services.length && <p className="py-8 text-center text-sm text-muted-foreground">Service status is currently unavailable.</p>}<div className="pt-4 text-xs text-muted-foreground"><p>Last successful sync: {<WorkspaceTime value={status.last_successful_sync}/>}</p><p>Snapshot generated: {<WorkspaceTime value={status.generated_at}/>}</p></div></CardContent></Card>
</div> }
function Summary({ label, value }: { label: string; value: string }) { return <Card><CardContent className="p-5"><p className="text-sm text-muted-foreground">{label}</p><p className="mt-1 text-2xl font-semibold">{value}</p></CardContent></Card> }
