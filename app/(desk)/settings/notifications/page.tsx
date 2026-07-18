import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { NotificationSettings } from "../preferences-client"
export const metadata={title:"Notifications"}
export default async function Page(){const request=await authenticatedBackendClient();const initial=await request<UserPreferences>("/api/user-preferences");return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Notifications" description="Choose which backend-confirmed events appear in the application."/><NotificationSettings initial={initial}/></div>}
