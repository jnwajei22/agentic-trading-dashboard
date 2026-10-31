import { AfdBackendError } from "@/lib/afd/errors"
import { notFound } from "next/navigation"
import { workspaceBackendClient } from "@/lib/afd/backend"
import type { ProfileContract } from "@/lib/afd/contracts"
import { ProfileEditor } from "./profile-editor"

export const metadata = { title: "Strategy" }
export default async function ProfilePage({ params }: { params: Promise<{ profileId: string }> }) {
  const { profileId } = await params; let contract: ProfileContract
  const request = await workspaceBackendClient()
  try { contract = await request<ProfileContract>(`/api/execution-profiles/${encodeURIComponent(profileId)}`) }
  catch(error) { if(error instanceof AfdBackendError && error.status===404) notFound(); throw error }
  return <ProfileEditor contract={contract} />
}
