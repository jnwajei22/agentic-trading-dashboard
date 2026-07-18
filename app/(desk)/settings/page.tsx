import Link from "next/link"
import { Bell, Eye, Lock, Palette, ShieldCheck, UserRoundCog } from "lucide-react"
import { PageHeader } from "@/components/desk/page-header"
import { Card,CardContent } from "@/components/ui/card"
const sections=[{href:"/settings/broker",icon:ShieldCheck,title:"Trading Connections",detail:"Add, verify, refresh, or remove broker-platform connections."},{href:"/settings/accounts",icon:UserRoundCog,title:"Trading Accounts",detail:"Manage primary account, nicknames, profiles, and availability."}]
const coming=[[Bell,"Notifications"],[Lock,"Security"],[Palette,"Appearance & timezone"],[Eye,"Data privacy"]] as const
export const metadata={title:"Settings"}
export default function SettingsPage(){return <div className="space-y-7"><PageHeader eyebrow="Workspace configuration" title="Settings" description="Manage trading connections, accounts, and application preferences. Credentials are never displayed."/><div className="grid gap-4 md:grid-cols-2">{sections.map(({href,icon:Icon,title,detail})=><Link href={href} key={href}><Card className="h-full hover:bg-accent"><CardContent className="flex gap-4 p-5"><Icon/><div><h2 className="font-semibold">{title}</h2><p className="text-sm text-muted-foreground">{detail}</p></div></CardContent></Card></Link>)}{coming.map(([Icon,title])=><Card key={title}><CardContent className="flex gap-4 p-5"><Icon/><div><h2 className="font-semibold">{title}</h2><p className="text-sm text-muted-foreground">Preference controls are coming soon.</p></div></CardContent></Card>)}</div></div>}
