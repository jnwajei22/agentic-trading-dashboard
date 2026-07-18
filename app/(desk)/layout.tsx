import { authenticatedBackendClient } from "@/lib/afd/backend"
import { requireSession } from "@/lib/afd/session"
import type { Account, BrokerStatus } from "@/lib/afd/contracts"
import { AppShell } from "@/components/desk/app-shell"

export const dynamic = "force-dynamic"

export default async function DeskLayout({ children }: { children: React.ReactNode }) {
  const session = await requireSession("/dashboard")
  let accounts: Account[] = []; let status: BrokerStatus | null = null; let backendOnline = false
  try {
    const request = await authenticatedBackendClient()
    const [accountResult, statusResult] = await Promise.allSettled([request<{ accounts: Account[] }>("/api/trading/accounts"), request<BrokerStatus>("/api/broker/status")])
    if (accountResult.status === "fulfilled") accounts = accountResult.value.accounts ?? []
    if (statusResult.status === "fulfilled") status = statusResult.value
    backendOnline = accountResult.status === "fulfilled" || statusResult.status === "fulfilled"
  } catch { /* shell defaults to safe unavailable state */ }
  return <AppShell user={session.user} accounts={accounts} backendOnline={backendOnline} brokerReady={status?.status === "ready"}>{children}</AppShell>
}
