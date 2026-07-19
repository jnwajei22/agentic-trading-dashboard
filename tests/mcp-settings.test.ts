import assert from "node:assert/strict"
import {readFileSync} from "node:fs"
import test from "node:test"
import type {McpSettings} from "../lib/afd/contracts"
import {chatGptConnectionStatus,copyPublicUrl} from "../lib/afd/mcp"

const source=(path:string)=>readFileSync(new URL(`../${path}`,import.meta.url),"utf8")
const settingsPage=source("app/(desk)/settings/page.tsx")
const page=source("app/(desk)/settings/integrations/mcp/page.tsx")
const surface=source("app/(desk)/settings/integrations/mcp/settings.tsx")
const instructions=source("components/settings/chatgpt-setup-instructions.tsx")

const response=(clients:McpSettings["authorized_clients"]=[]):McpSettings=>({
  display_name:"Agentic Trading Desk",server_url:"https://mcp.example.test/mcp",resource_uri:"https://mcp.example.test",
  protected_resource_metadata_url:"https://mcp.example.test/.well-known/oauth-protected-resource",authorization_server_issuer:"https://mcp.example.test",
  authentication_required:true,authentication_available:true,status:"available",supported_scopes:[{scope:"forex:read",label:"View Accounts and Markets",description:"Safe read access."}],
  unsupported_scopes:[{scope:"trade:submit:live",label:"Submit Live Trades",description:"Unsupported."}],authorized_clients:clients,
  authorized_clients_status:"available",revocation_supported:true,setup_url:null,protocol_status:"available",
})

test("Settings links to the focused ChatGPT and MCP destination",()=>{
  assert.match(settingsPage,/ChatGPT & MCP/);assert.match(settingsPage,/\/settings\/integrations\/mcp/)
  assert.match(settingsPage,/Connect Agentic Trading Desk to ChatGPT and manage authorized AI applications/)
})

test("MCP settings route is dynamic authenticated and backend sourced",()=>{
  assert.match(page,/export const dynamic="force-dynamic"/);assert.match(page,/requireSession\("\/settings\/integrations\/mcp"\)/)
  assert.match(page,/\/api\/integrations\/mcp/);assert.match(page,/title="ChatGPT Connection"/)
  assert.match(page,/Use Agentic Trading Desk securely from ChatGPT through your existing account/)
})

test("public MCP URL renders and copy provides success feedback",async()=>{
  let copied="";const message=await copyPublicUrl({writeText:async value=>{copied=value}},"https://mcp.example.test/mcp")
  assert.equal(copied,"https://mcp.example.test/mcp");assert.equal(message,"MCP URL copied.")
  assert.match(surface,/settings\.server_url/);assert.match(surface,/Copy MCP URL/);assert.match(surface,/navigator\.clipboard/)
})

test("setup instructions are reusable resilient and plan aware",()=>{
  for(const phrase of ["Open ChatGPT settings","Apps, Connectors, or Developer Mode","custom MCP app or connector","permission flow","Return to ChatGPT"])assert.match(instructions,new RegExp(phrase))
  assert.match(instructions,/depend on your ChatGPT plan and workspace settings/)
  assert.match(surface,/ChatGptSetupInstructions/)
})

test("connection state comes from backend grants and never dashboard login",()=>{
  assert.equal(chatGptConnectionStatus(response()),"Not Connected")
  assert.equal(chatGptConnectionStatus({...response(),authorized_clients:null,authorized_clients_status:"unavailable"}),"Unavailable")
  assert.equal(chatGptConnectionStatus(response([{grant_id:"grant_safe",client_name:"ChatGPT",client_type:"mcp",connected_at:"2026-07-19T00:00:00Z",last_used_at:null,granted_scopes:["forex:read"],status:"active"}])),"Connected")
  assert.doesNotMatch(surface,/getSession|session\.user|dashboard.*connected/i)
})

test("authorized grants use customer permissions and safe structured fields",()=>{
  assert.match(surface,/authorized_clients\.map/);assert.match(surface,/client\.client_name/);assert.match(surface,/client\.connected_at/);assert.match(surface,/client\.last_used_at/)
  assert.match(surface,/labels\.get\(scope\)/);assert.match(surface,/Granted|Not Granted|Available|Unsupported/)
  assert.doesNotMatch(surface,/access_token|refresh_token|client_secret|authorization_code|code_verifier|oauth_state|raw claims/i)
})

test("disconnect is supported only with confirmation and preserves dashboard connections",()=>{
  assert.match(surface,/revocation_supported/);assert.match(surface,/Disconnect ChatGPT\?/)
  assert.match(surface,/Your dashboard and trading connections will remain active/)
  assert.match(surface,/AlertDialogCancel>Cancel/);assert.match(surface,/Disconnect ChatGPT<\/AlertDialogAction>/)
})

test("advanced public details are collapsed and live submission is never granted",()=>{
  assert.match(surface,/<details/);assert.doesNotMatch(surface,/<details[^>]*open/);assert.match(surface,/Advanced Details/)
  assert.match(surface,/unsupported_scopes\.map/);assert.doesNotMatch(response().unsupported_scopes[0].scope,/granted/i)
})

test("connection surface wraps safely on mobile without horizontal overflow",()=>{
  assert.match(surface,/grid min-w-0 gap-4 sm:grid-cols-2/);assert.match(surface,/break-all/);assert.match(surface,/flex flex-wrap/)
  assert.doesNotMatch(surface,/overflow-x-auto|w-screen|min-w-\[/)
})
