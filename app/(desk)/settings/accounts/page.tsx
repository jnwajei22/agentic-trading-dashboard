import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { AccountSettings } from "./settings"

export const metadata = { title: "Trading Accounts" }
export default async function AccountSettingsPage() { let accounts: Account[] = []; try { accounts = (await (await authenticatedBackendClient())<{ accounts: Account[] }>("/api/trading/accounts")).accounts ?? [] } catch { /* unavailable */ } return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Trading Accounts" description="Manage primary account, optional nicknames, bound strategies, platform metadata, and availability." /><AccountSettings accounts={accounts} /></div> }
