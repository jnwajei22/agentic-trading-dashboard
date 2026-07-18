import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { RegionalSettings } from "../preferences-client"
export const metadata={title:"Timezone and Regional Settings"}
export default async function Page(){const initial=await (await authenticatedBackendClient())<UserPreferences>("/api/user-preferences");return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Timezone and Regional Settings" description="Schedules default to your stored IANA timezone; date, time, and currency formatting remain display preferences."/><RegionalSettings initial={initial}/></div>}
