import { workspaceBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { AppearanceSettings } from "../preferences-client"

export const metadata = { title: "Appearance" }
export default async function Page() { const initial = await (await workspaceBackendClient())<UserPreferences>("/api/user-preferences"); return <div className="space-y-7"><SettingsPageHeader title="Appearance" description="Choose how Agentic Trading Desk looks on this device." /><AppearanceSettings initial={initial} /></div> }
