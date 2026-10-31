"use client"
import {useState} from "react"
import {Download} from "lucide-react"
import {browserAfdFetch} from "@/lib/afd/browser-client"
import {Button} from "@/components/ui/button"
export function ExportWorkspace(){const [busy,setBusy]=useState(false);const [error,setError]=useState("");async function download(){setBusy(true);setError("");try{const result=await browserAfdFetch("workspace-export");const url=URL.createObjectURL(new Blob([JSON.stringify(result,null,2)],{type:"application/json"}));const link=document.createElement("a");link.href=url;link.download=`agentic-workspace-${new Date().toISOString().slice(0,10)}.json`;link.click();URL.revokeObjectURL(url)}catch(error){setError(error instanceof Error?error.message:"Export failed. Try again.")}finally{setBusy(false)}}return <div><Button variant="outline" disabled={busy} onClick={()=>void download()}><Download className="mr-2 h-4 w-4"/>{busy?"Preparing export…":"Export workspace"}</Button>{error&&<p role="alert" className="mt-3 text-sm text-red-500">{error}</p>}</div>}
