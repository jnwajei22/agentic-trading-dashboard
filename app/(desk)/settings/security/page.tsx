import Link from "next/link"
import { authenticatedBackendClient } from "@/lib/afd/backend"
import { requireSession } from "@/lib/afd/session"
import type { Connection } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { Button } from "@/components/ui/button"
import { Card,CardContent,CardHeader,CardTitle } from "@/components/ui/card"
export const metadata={title:"Security"}
export default async function Page(){const session=await requireSession("/settings/security");let connections:Connection[]=[];try{connections=(await (await authenticatedBackendClient())<{connections:Connection[]}>("/api/broker/connections")).connections}catch{}return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Security" description="Your authenticated session and connected trading platforms."/><Card><CardHeader><CardTitle>Current session</CardTitle></CardHeader><CardContent className="space-y-3 text-sm"><p><span className="text-muted-foreground">Authenticated email:</span> {session.user.email??"Unavailable"}</p><p><span className="text-muted-foreground">Session:</span> Active in this browser</p><p><span className="text-muted-foreground">Connected platforms:</span> {connections.length?"TradeLocker":"None"}</p><p><span className="text-muted-foreground">Credential last updated:</span> {connections[0]?.last_verified_at??"Not recorded"}</p><div className="flex gap-2"><Button asChild><Link href="/auth/logout">Sign out</Link></Button><Button asChild variant="outline"><Link href="/settings/broker">Manage trading connections</Link></Button></div></CardContent></Card></div>}
