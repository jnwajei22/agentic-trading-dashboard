import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account, ProfileSummary } from "@/lib/afd/contracts"
import { MarketsWorkspace } from "./workspace"
import { PageHeader } from "@/components/desk/page-header"

export const metadata = { title: "Markets" }
export default async function MarketsPage() {
  let accounts: Account[] = []; let profiles: ProfileSummary[] = []
  try { const request = await authenticatedBackendClient(); const [a, p] = await Promise.all([request<{ accounts: Account[] }>("/api/broker/accounts"), request<{ profiles: ProfileSummary[] }>("/api/execution-profiles")]); accounts = a.accounts ?? []; profiles = p.profiles ?? [] } catch { /* unavailable is rendered safely */ }
  return <div className="space-y-7"><PageHeader eyebrow="Market discovery" title="Markets workspace" description="Search the selected TradeLocker account’s instrument catalog and compare the broker universe with execution-profile scope." /><MarketsWorkspace accounts={accounts} profiles={profiles} /></div>
}
