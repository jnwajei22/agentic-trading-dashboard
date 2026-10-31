import { workspaceBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { NotificationSettings } from "../preferences-client"

export const metadata = { title: "Notifications" }
export default async function Page() { const initial = await (await workspaceBackendClient())<UserPreferences>("/api/user-preferences"); return <div className="space-y-7"><SettingsPageHeader title="Notifications" description="Choose which supported trading and automation events appear in the application." /><NotificationSettings initial={initial} /></div> }
