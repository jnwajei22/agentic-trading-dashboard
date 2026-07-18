import { authenticatedBackendClient } from "@/lib/afd/backend"
import { formatEnum } from "@/lib/afd/display"
import { PageHeader } from "@/components/desk/page-header"
import { Card,CardContent,CardHeader,CardTitle } from "@/components/ui/card"
import { StatusPill } from "@/components/desk/status-pill"
type Service={id:string;label:string;status:string};type Status={services:Service[];last_successful_sync?:string;generated_at:string}
export const metadata={title:"Connection Status"}
export default async function Page(){let status:Status={services:[],generated_at:new Date().toISOString()};try{status=await (await authenticatedBackendClient())<Status>("/api/status")}catch{}return <div className="space-y-7"><PageHeader eyebrow="Diagnostics" title="Connection Status" description="User-safe service availability without internal processes, hosts, or worker identifiers."/><Card><CardHeader><CardTitle>Services</CardTitle></CardHeader><CardContent className="divide-y">{status.services.map(service=><div key={service.id} className="flex justify-between py-3"><span>{service.label}</span><StatusPill label={formatEnum(service.status)} tone={["available","connected"].includes(service.status)?"good":service.status==="needs_attention"?"warn":"bad"}/></div>)}{!status.services.length&&<p className="py-6 text-sm text-muted-foreground">Status is unavailable.</p>}<p className="pt-4 text-xs text-muted-foreground">Last successful sync: {status.last_successful_sync?new Date(status.last_successful_sync).toLocaleString():"Not recorded"}</p></CardContent></Card></div>}
