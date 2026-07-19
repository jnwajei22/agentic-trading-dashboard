import type {McpAuthorizedClient,McpSettings} from "./contracts"

export type ClipboardWriter={writeText(value:string):Promise<void>}

export async function copyPublicUrl(clipboard:ClipboardWriter,url:string){
  await clipboard.writeText(url)
  return "MCP URL copied."
}

export function chatGptConnectionStatus(settings:McpSettings|null){
  if(!settings)return "Unavailable"
  if(settings.status==="needs_attention")return "Needs Attention"
  if(settings.authorized_clients_status==="unavailable")return "Unavailable"
  const clients=settings.authorized_clients??[]
  if(clients.some(client=>client.status==="active"))return "Connected"
  if(clients.some(client=>client.status==="authorization_expired"))return "Authorization Expired"
  return "Not Connected"
}

export function activeAuthorizedClient(clients:McpAuthorizedClient[]|null){
  return clients?.find(client=>client.status==="active")??null
}
