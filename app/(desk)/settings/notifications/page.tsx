import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { NotificationSettings } from "../preferences-client"

export const metadata = { title: "Notifications" }
const fallback: UserPreferences = { appearance: "system", timezone: "America/Chicago", date_format: "locale", time_format: "locale", currency_display: "account", notifications: {}, updated_at: new Date(0).toISOString() }
export default async function Page() { let initial = fallback; try { initial = await (await authenticatedBackendClient())<UserPreferences>("/api/user-preferences") } catch {} return <div className="space-y-7"><SettingsPageHeader title="Notifications" description="Choose which supported trading and automation events appear in the application." /><NotificationSettings initial={initial} /></div> }
