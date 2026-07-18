import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Connection,ProviderDescriptor } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { BrokerSettings } from "./settings"

export const metadata = { title: "Trading Connections" }
export default async function BrokerSettingsPage() { let connections: Connection[] = [];let providers:ProviderDescriptor[]=[]; try { const request=await authenticatedBackendClient();const [c,p]=await Promise.all([request<{connections:Connection[]}>("/api/trading/connections"),request<{providers:ProviderDescriptor[]}>("/api/providers")]);connections=c.connections??[];providers=p.providers??[] } catch { /* unavailable */ } return <div className="space-y-7"><PageHeader eyebrow="Settings" title="Trading Connections" description="Connect brokers, execution platforms, chart providers, and signal providers without exposing provider secrets to the browser." /><BrokerSettings connections={connections} providers={providers} /></div> }
