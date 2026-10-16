import { workspaceBackendClient } from "@/lib/afd/backend"
import { workspaceSession } from "@/lib/afd/session"
import { AccountSelector } from "./selector"

export const metadata = { title: "Select account" }
export const dynamic = "force-dynamic"
export default async function SelectAccountPage({ searchParams }: { searchParams: Promise<{ connection_id?: string }> }) {
  await workspaceSession()
  const connectionId = (await searchParams).connection_id
  let accounts: Array<Record<string, unknown>> = []; let error = ""
  try { const request = await workspaceBackendClient(); const result = await request<{ accounts?: Array<Record<string, unknown>> }>(`/api/broker/tradelocker/discover-accounts${connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : ""}`, { method: "POST" }); accounts = result.accounts ?? [] } catch { error = "Accounts could not be discovered. Reauthenticate or try again." }
  return <main className="grid min-h-screen place-items-center bg-muted/30 p-4"><AccountSelector accounts={accounts} connectionId={connectionId} initialError={error} /></main>
}
