import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Connection } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { BrokerSettings } from "./settings"

export const metadata = { title: "Broker settings" }
export default async function BrokerSettingsPage() { let connections: Connection[] = []; try { connections = (await (await authenticatedBackendClient())<{ connections: Connection[] }>("/api/broker/connections")).connections ?? [] } catch { /* unavailable */ } return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Broker connections" description="Manage encrypted TradeLocker connections through the existing backend credential store." /><BrokerSettings connections={connections} /></div> }
