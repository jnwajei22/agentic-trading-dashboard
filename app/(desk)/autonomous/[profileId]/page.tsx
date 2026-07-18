import { notFound } from "next/navigation"
import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { ProfileContract } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { ProfileEditor } from "./profile-editor"

export const metadata = { title: "Execution Profile V2" }
export default async function ProfilePage({ params }: { params: Promise<{ profileId: string }> }) {
  const { profileId } = await params; let contract: ProfileContract
  try { const request = await authenticatedBackendClient(); const [profile, capabilities] = await Promise.all([request<ProfileContract>(`/api/execution-profiles/${encodeURIComponent(profileId)}`), request<Record<string, unknown>>(`/api/execution-profiles/${encodeURIComponent(profileId)}/capabilities`)]); contract = { ...profile, account_capabilities: { ...profile.account_capabilities, ...capabilities } } } catch { notFound() }
  return <div className="space-y-7"><PageHeader eyebrow="Execution Profile V2" title={`Profile · ${contract.account_number ?? "Trading account"}`} description={`${contract.broker_name ?? "Broker"} · ${contract.account_environment ?? "Account"}${contract.nickname ? ` · ${contract.nickname}` : ""}. Backend capabilities and validation drive this editor.`} /><ProfileEditor contract={contract} /></div>
}
