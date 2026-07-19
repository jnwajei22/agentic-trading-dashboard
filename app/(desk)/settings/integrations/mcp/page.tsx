import {authenticatedBackendClient} from "@/lib/afd/backend"
import type {McpSettings} from "@/lib/afd/contracts"
import {requireSession} from "@/lib/afd/session"
import {SettingsPageHeader} from "@/components/settings/settings-page"
import {McpConnectionSettings} from "./settings"

export const dynamic="force-dynamic"
export const metadata={title:"ChatGPT Connection"}

export default async function Page(){
  await requireSession("/settings/integrations/mcp")
  let settings:McpSettings|null=null
  try{settings=await (await authenticatedBackendClient())<McpSettings>("/api/integrations/mcp")}catch{}
  return <div className="space-y-7"><SettingsPageHeader title="ChatGPT & MCP" description="Connect Agentic Trading Desk to ChatGPT and manage authorized AI applications."/><McpConnectionSettings initialSettings={settings}/></div>
}
