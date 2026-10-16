"use client"
import { useEffect } from "react"
export type PageContext = { market?: string | null; profile_ref?: string | null; schedule_id?: string | null; preview_id?: string | null; run_id?: string | null }
let current: PageContext = {}
export function currentAssistantContext() { return current }
export function useAssistantContext(context: PageContext) {
  const serialized = JSON.stringify(context)
  useEffect(() => {
    current = JSON.parse(serialized)
    window.dispatchEvent(new CustomEvent("desk-assistant-context", { detail: JSON.parse(serialized) }))
    return () => { window.dispatchEvent(new CustomEvent("desk-assistant-context", { detail: {} })) }
  }, [serialized])
}
