import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Connection,ProviderDescriptor } from "@/lib/afd/contracts"
import { SettingsPageHeader } from "@/components/settings/settings-page"
import { BrokerSettings } from "./settings"

export const metadata = { title: "Trading Connections" }
export default async function BrokerSettingsPage() { let connections: Connection[] = [];let providers:ProviderDescriptor[]=[]; try { const request=await authenticatedBackendClient();const [c,p]=await Promise.all([request<{connections:Connection[]}>("/api/trading/connections"),request<{providers:ProviderDescriptor[]}>("/api/providers")]);connections=c.connections??[];providers=p.providers??[] } catch { /* unavailable */ } return <div className="space-y-7"><SettingsPageHeader title="Trading Connections" description="Connect and manage execution, chart, and signal providers without exposing credentials to the browser." /><BrokerSettings connections={connections} providers={providers} /></div> }
