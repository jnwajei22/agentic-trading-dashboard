import {authenticatedBackendClient} from "@/lib/afd/backend"
import type {McpSettings} from "@/lib/afd/contracts"
import {requireSession} from "@/lib/afd/session"
import {PageHeader} from "@/components/desk/page-header"
import {McpConnectionSettings} from "./settings"

export const dynamic="force-dynamic"
export const metadata={title:"ChatGPT Connection"}

export default async function Page(){
  await requireSession("/settings/integrations/mcp")
  let settings:McpSettings|null=null
  try{settings=await (await authenticatedBackendClient())<McpSettings>("/api/integrations/mcp")}catch{}
  return <div className="space-y-7"><PageHeader eyebrow="Settings" title="ChatGPT Connection" description="Use Agentic Trading Desk securely from ChatGPT through your existing account."/><McpConnectionSettings initialSettings={settings}/></div>
}
