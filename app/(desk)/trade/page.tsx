import { PageHeader } from "@/components/desk/page-header"
import { TradeWorkspace } from "./workspace"
export const metadata={title:"Trade"}
export default function TradePage(){return <div className="space-y-7"><PageHeader eyebrow="Trade" title="Trade" description="Understand the price, size, and risk of your next move."/><TradeWorkspace/></div>}
