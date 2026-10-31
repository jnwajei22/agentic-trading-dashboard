import { workspaceBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { RegionalSettings } from "../preferences-client"

export const metadata = { title: "Regional Settings" }
export default async function Page() { const initial = await (await workspaceBackendClient())<UserPreferences>("/api/user-preferences"); return <div className="space-y-7"><SettingsPageHeader title="Regional Settings" description="Set the timezone and display formats used throughout the workspace." /><RegionalSettings initial={initial} /></div> }
