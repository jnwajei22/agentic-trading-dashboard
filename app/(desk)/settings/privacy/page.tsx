import Link from "next/link"
import { Database, Download, KeyRound, Trash2 } from "lucide-react"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export const metadata = { title: "Data and Privacy" }
export default function Page() { return <div className="space-y-7"><SettingsPageHeader title="Data and Privacy" description="Review what the application stores and the supported export or removal workflows." />
  <div className="grid gap-4 lg:grid-cols-2">
    <Card><CardHeader><CardTitle className="flex items-center gap-2"><KeyRound className="h-5 w-5" />Broker credentials</CardTitle></CardHeader><CardContent className="space-y-3 text-sm"><p>Broker credentials are encrypted by the backend and are never returned to the browser.</p><Button asChild variant="outline"><Link href="/settings/broker">Manage trading connections</Link></Button></CardContent></Card>
    <Card><CardHeader><CardTitle className="flex items-center gap-2"><Database className="h-5 w-5" />Audit records</CardTitle></CardHeader><CardContent className="space-y-3 text-sm"><p>Audit records store structured decisions, controls, outcomes, and reconciliation data. They do not store private reasoning.</p><Button asChild variant="outline"><Link href="/activity"><Download className="mr-2 h-4 w-4" />Export filtered activity</Link></Button></CardContent></Card>
  </div>
  <Card><CardHeader><CardTitle className="flex items-center gap-2"><Trash2 className="h-5 w-5" />Account data removal</CardTitle><p className="text-sm text-muted-foreground">Full account-data deletion requires a verified support workflow and is not yet available in the dashboard.</p></CardHeader><CardContent><Button variant="outline" disabled>Request account-data deletion</Button></CardContent></Card>
</div> }
