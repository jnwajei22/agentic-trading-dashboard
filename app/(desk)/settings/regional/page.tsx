import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { RegionalSettings } from "../preferences-client"

export const metadata = { title: "Regional Settings" }
const fallback: UserPreferences = { appearance: "system", timezone: "America/Chicago", date_format: "locale", time_format: "locale", currency_display: "account", notifications: {}, updated_at: new Date(0).toISOString() }
export default async function Page() { let initial = fallback; try { initial = await (await authenticatedBackendClient())<UserPreferences>("/api/user-preferences") } catch {} return <div className="space-y-7"><SettingsPageHeader title="Regional Settings" description="Set the timezone and display formats used throughout the workspace." /><RegionalSettings initial={initial} /></div> }
