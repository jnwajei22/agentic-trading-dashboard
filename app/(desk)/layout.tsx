import { workspaceBackendClient } from "@/lib/afd/backend"
import { workspaceSession } from "@/lib/afd/session"
import type { Account } from "@/lib/afd/contracts"
import { AppShell } from "@/components/desk/app-shell"

export const dynamic = "force-dynamic"

export default async function DeskLayout({ children }: { children: React.ReactNode }) {
  const session = await workspaceSession()
  let accounts: Account[] = []
  try {
    const request = await workspaceBackendClient()
    const result = await request<{ accounts: Account[] }>("/api/trading/accounts")
    accounts = result.accounts ?? []
  } catch { /* Account switching remains unavailable until accounts load. */ }
  return <AppShell user={session.user} accounts={accounts}>{children}</AppShell>
}
