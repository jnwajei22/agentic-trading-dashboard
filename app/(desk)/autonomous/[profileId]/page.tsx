import { notFound } from "next/navigation"
import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { ProfileContract,ProfileSummary } from "@/lib/afd/contracts"
import { ProfileEditor } from "./profile-editor"

export const metadata = { title: "Strategy" }
export default async function ProfilePage({ params }: { params: Promise<{ profileId: string }> }) {
  const { profileId } = await params; let contract: ProfileContract
  try { const request = await authenticatedBackendClient(); const [profile, capabilities,listing] = await Promise.all([request<ProfileContract>(`/api/execution-profiles/${encodeURIComponent(profileId)}`), request<Record<string, unknown>>(`/api/execution-profiles/${encodeURIComponent(profileId)}/capabilities`),request<{profiles:ProfileSummary[]}>("/api/execution-profiles")]); const listed=listing.profiles.find(item=>item.public_id===profileId);contract = { ...profile,name:listed?.name, account_capabilities: { ...profile.account_capabilities, ...capabilities } } } catch { notFound() }
  return <ProfileEditor contract={contract} />
}
