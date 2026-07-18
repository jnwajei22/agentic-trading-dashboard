import Link from "next/link"
import { PageHeader } from "@/components/desk/page-header"
import { Button } from "@/components/ui/button"
import { Card,CardContent,CardHeader,CardTitle } from "@/components/ui/card"
export const metadata={title:"Data and Privacy"}
export default function Page(){return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Data and Privacy" description="Understand stored data and use supported removal workflows."/><Card><CardHeader><CardTitle>Your data</CardTitle></CardHeader><CardContent className="space-y-4 text-sm"><p>Broker credentials are encrypted by the backend and are never returned to the browser. Audit records retain structured outcomes, controls, and execution reconciliation—not private reasoning.</p><div className="flex flex-wrap gap-2"><Button asChild><Link href="/activity">Export filtered activity</Link></Button><Button asChild variant="outline"><Link href="/settings/broker">Remove broker credentials</Link></Button><Button variant="outline" disabled>Delete user data request — support workflow required</Button></div></CardContent></Card></div>}
