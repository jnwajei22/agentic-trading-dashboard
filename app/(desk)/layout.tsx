import { redirect } from "next/navigation"
import { auth0 } from "@/lib/afd/auth"
import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account, BrokerStatus } from "@/lib/afd/contracts"
import { AppShell } from "@/components/desk/app-shell"

export default async function DeskLayout({ children }: { children: React.ReactNode }) {
  const session = await auth0.getSession().catch(() => null)
  if (!session) redirect("/login")
  let accounts: Account[] = []; let status: BrokerStatus | null = null; let backendOnline = false
  try {
    const request = await authenticatedBackendClient()
    const [accountResult, statusResult] = await Promise.allSettled([request<{ accounts: Account[] }>("/api/broker/accounts"), request<BrokerStatus>("/api/broker/status")])
    if (accountResult.status === "fulfilled") accounts = accountResult.value.accounts ?? []
    if (statusResult.status === "fulfilled") status = statusResult.value
    backendOnline = accountResult.status === "fulfilled" || statusResult.status === "fulfilled"
  } catch { /* shell defaults to safe unavailable state */ }
  return <AppShell user={session.user} accounts={accounts} backendOnline={backendOnline} brokerReady={status?.status === "ready"}>{children}</AppShell>
}
