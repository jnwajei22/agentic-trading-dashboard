import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { AccountSettings } from "./settings"

export const metadata = { title: "Trading Accounts" }
export default async function AccountSettingsPage() { let accounts: Account[] = []; try { accounts = (await (await authenticatedBackendClient())<{ accounts: Account[] }>("/api/trading/accounts")).accounts ?? [] } catch { /* unavailable */ } return <div className="space-y-7"><SettingsPageHeader title="Trading Accounts" description="Manage account names, primary selection, capabilities, and local availability." /><AccountSettings accounts={accounts} /></div> }
