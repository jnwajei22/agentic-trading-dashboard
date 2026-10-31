import { workspaceBackendClient } from "@/lib/afd/backend"
import { workspaceSession } from "@/lib/afd/session"
import type { Account,UserPreferences } from "@/lib/afd/contracts"
import { AppShell } from "@/components/desk/app-shell"

export const dynamic = "force-dynamic"

export default async function DeskLayout({ children }: { children: React.ReactNode }) {
  const session = await workspaceSession()
  let accounts: Account[] = [];let preferences:UserPreferences|null=null
  try {
    const request = await workspaceBackendClient()
    const [accountResult,prefsResult]=await Promise.allSettled([request<{accounts:Account[]}>("/api/trading/accounts"),request<UserPreferences>("/api/user-preferences")])
    if(accountResult.status==="fulfilled")accounts=accountResult.value.accounts??[]
    if(prefsResult.status==="fulfilled")preferences=prefsResult.value
  } catch { /* Individual pages expose unavailable services. */ }
  return <AppShell user={session.user} accounts={accounts} preferences={preferences}>{children}</AppShell>
}
