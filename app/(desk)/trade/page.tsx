import { PageHeader } from "@/components/desk/page-header"
import { TradeWorkspace } from "./workspace"
export const metadata={title:"Trade"}
export default function TradePage(){return <div className="space-y-7"><PageHeader eyebrow="Trade" title="Order Workspace" description="Build and review an order safely. Submission remains disabled until authenticated backend trading REST contracts explicitly support it."/><TradeWorkspace/></div>}
