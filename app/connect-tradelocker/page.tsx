import { requireSession } from "@/lib/afd/session"
import { BrokerConnectForm } from "./broker-connect-form"

export const metadata = { title: "Connect TradeLocker" }
export const dynamic = "force-dynamic"
export default async function ConnectTradeLockerPage({ searchParams }: { searchParams: Promise<{ connection_id?: string; new?: string }> }) {
  await requireSession("/connect-tradelocker")
  const params = await searchParams
  return <main className="grid min-h-screen place-items-center bg-muted/30 p-4"><BrokerConnectForm connectionId={params.connection_id} createNew={params.new === "1"} /></main>
}
