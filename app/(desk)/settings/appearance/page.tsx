import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { UserPreferences } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { AppearanceSettings } from "../preferences-client"
export const metadata={title:"Appearance"}
export default async function Page(){const initial=await (await authenticatedBackendClient())<UserPreferences>("/api/user-preferences");return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Appearance" description="Persist light, dark, or system appearance without exposing runtime configuration."/><AppearanceSettings initial={initial}/></div>}
