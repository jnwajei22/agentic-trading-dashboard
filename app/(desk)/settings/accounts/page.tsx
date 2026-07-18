import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { AccountSettings } from "./settings"

export const metadata = { title: "Account settings" }
export default async function AccountSettingsPage() { let accounts: Account[] = []; try { accounts = (await (await authenticatedBackendClient())<{ accounts: Account[] }>("/api/broker/accounts")).accounts ?? [] } catch { /* unavailable */ } return <div className="space-y-7"><PageHeader eyebrow="Settings" title="TradeLocker accounts" description="Manage safe account labels and default analysis context. Environment and profile binding remain backend-authoritative." /><AccountSettings accounts={accounts} /></div> }
