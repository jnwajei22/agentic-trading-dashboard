import { redirect } from "next/navigation"
import { auth0 } from "@/lib/afd/auth"
import { BrokerConnectForm } from "./broker-connect-form"

export const metadata = { title: "Connect TradeLocker" }
export default async function ConnectTradeLockerPage({ searchParams }: { searchParams: Promise<{ connection_id?: string; new?: string }> }) {
  if (!await auth0.getSession().catch(() => null)) redirect("/login")
  const params = await searchParams
  return <main className="grid min-h-screen place-items-center bg-muted/30 p-4"><BrokerConnectForm connectionId={params.connection_id} createNew={params.new === "1"} /></main>
}
