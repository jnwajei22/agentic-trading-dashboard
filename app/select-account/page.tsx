import { redirect } from "next/navigation"
import { auth0 } from "@/lib/afd/auth"
import { authenticatedBackendClient } from "@/lib/afd/backend"
import { AccountSelector } from "./selector"

export const metadata = { title: "Select account" }
export default async function SelectAccountPage({ searchParams }: { searchParams: Promise<{ connection_id?: string }> }) {
  if (!await auth0.getSession().catch(() => null)) redirect("/login")
  const connectionId = (await searchParams).connection_id
  let accounts: Array<Record<string, unknown>> = []; let error = ""
  try { const request = await authenticatedBackendClient(); const result = await request<{ accounts?: Array<Record<string, unknown>> }>(`/api/broker/tradelocker/discover-accounts${connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : ""}`, { method: "POST" }); accounts = result.accounts ?? [] } catch { error = "Accounts could not be discovered. Reauthenticate or try again." }
  return <main className="grid min-h-screen place-items-center bg-muted/30 p-4"><AccountSelector accounts={accounts} connectionId={connectionId} initialError={error} /></main>
}
