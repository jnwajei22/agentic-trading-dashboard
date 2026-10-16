"use client"

import { useEffect, useRef, useState } from "react"
import { usePathname, useSearchParams } from "next/navigation"
import { AssistantResult } from "./assistant-result"
import { Bot, X, Square, Send } from "lucide-react"
import { currentAssistantContext, type PageContext } from "@/lib/afd/assistant-context"
import { Button } from "@/components/ui/button"
import { useAccountContext } from "./account-provider"
import { browserAfdFetch, mutateAfd } from "@/lib/afd/browser-client"
import { consumeAssistantStream, type AssistantEvent } from "@/lib/afd/assistant"

type Profile = { public_id: string; name: string; account_id: string }
type Message = { kind: string; text?: string; tool?: string; result?: Record<string, unknown> }
type History = { id: string; mode: string; profile_ref: string | null }

export function AssistantPanel() {
  const [pageContext, setPageContext] = useState<PageContext>({})
  const [open, setOpen] = useState(false)
  const [mode, setMode] = useState("research")
  const [profile, setProfile] = useState("")
  const [profiles, setProfiles] = useState<Profile[]>([])
  const [threads, setThreads] = useState<History[]>([])
  const [thread, setThread] = useState("")
  const [messages, setMessages] = useState<Message[]>([])
  const [input, setInput] = useState("")
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [used, setUsed] = useState<string[]>([])
  const abort = useRef<AbortController | null>(null)
  const scroll = useRef<HTMLDivElement | null>(null)
  const pathname = usePathname(); const query = useSearchParams()
  const { selected } = useAccountContext()
  const pageProfile = pathname.startsWith("/autonomous/") ? pathname.split("/").at(-1) : null
  const boundProfile = profile || pageContext.profile_ref || pageProfile || null
  const context = { ...pageContext, page: pathname, market: pageContext.market || query.get("instrument") || query.get("symbol"),
    account_ref: selected?.publicRef ?? null, profile_ref: boundProfile,
    schedule_id: pageContext.schedule_id || query.get("schedule_id"), run_id: pageContext.run_id || query.get("run_id"), preview_id: pageContext.preview_id || query.get("preview_id") }

  useEffect(() => {
    setPageContext(currentAssistantContext())
    const update = (event: Event) => setPageContext((event as CustomEvent<PageContext>).detail)
    window.addEventListener("desk-assistant-context", update)
    return () => window.removeEventListener("desk-assistant-context", update)
  }, [])
  useEffect(() => {
    if (!open) return
    let active = true
    void Promise.all([browserAfdFetch<{ profiles: Profile[] }>("execution-profiles"), browserAfdFetch<{ conversations: History[] }>("assistant/conversations")])
      .then(([p, h]) => { if (active) { setProfiles(p.profiles); setThreads(h.conversations) } })
      .catch(() => { if (active) setError("Unable to load assistant history or profiles.") })
    return () => { active = false }
  }, [open])
  useEffect(() => { scroll.current?.scrollIntoView({ block: "end" }) }, [messages])
  useEffect(() => () => abort.current?.abort(), [])

  function reset() { setThread(""); setMessages([]); setUsed([]); setError("") }
  async function restore(id: string) {
    setError("")
    try {
      const data = await browserAfdFetch<{ conversation: History; messages: { kind: string; payload: Record<string, unknown> }[] }>(`assistant/conversations/${id}`)
      setThread(id); setMode(data.conversation.mode); setProfile(data.conversation.profile_ref ?? "")
      setMessages(data.messages.filter(m => ["user", "assistant", "tool", "execution"].includes(m.kind)).map(m => ({ kind: m.kind, text: typeof m.payload.text === "string" ? m.payload.text : undefined,
        tool: typeof m.payload.tool === "string" ? m.payload.tool : undefined,
        result: m.kind === "execution" ? m.payload : m.payload.result as Record<string, unknown> | undefined })))
    } catch { setError("Conversation could not be loaded.") }
  }
  function receive(event: AssistantEvent) {
    if (event.type === "conversation" && event.conversation_id) setThread(event.conversation_id)
    if (event.type === "delta") setMessages(old => {
      const last = old.at(-1)
      return last?.kind === "assistant" ? [...old.slice(0, -1), { ...last, text: (last.text ?? "") + event.text }] : [...old, { kind: "assistant", text: event.text }]
    })
    if (event.type === "tool_start") setMessages(old => [...old, { kind: "progress", text: `Using ${event.tool?.replaceAll("_", " ")}…` }])
    if (event.type === "tool_result") setMessages(old => [...old, { kind: "tool", tool: event.tool, result: event.result }])
    if (event.type === "error") setError(event.code === "assistant_unavailable" ? "Assistant unavailable. Check server configuration or try again. Verify trading activity before retrying an action." : "The assistant reached its tool limit.")
  }
  async function send() {
    if (!input.trim() || busy) return
    const message = input.trim(); setInput(""); setError(""); setBusy(true)
    setMessages(old => [...old, { kind: "user", text: message }])
    abort.current = new AbortController()
    let completed = false
    try {
      const response = await fetch("/api/assistant/stream", { method: "POST", headers: { "Content-Type": "application/json" }, signal: abort.current.signal,
        body: JSON.stringify({ conversation_id: thread || null, message, mode, context }) })
      if (!response.ok || !response.body) throw new Error((await response.json()).error || "Assistant could not start.")
      await consumeAssistantStream(response.body, event => { if (event.type === "done" || event.type === "error") completed = true; receive(event) })
      if (!completed) throw new Error("Response interrupted. Check activity before retrying an action.")
    } catch (e) { setError(e instanceof Error && e.name !== "AbortError" ? e.message : "Response stopped. Already submitted actions may still finish; check Activity.") }
    finally { setBusy(false); abort.current = null }
  }
  async function approve(result: Record<string, unknown>) {
    const id = String(result.approval_id); setUsed(old => [...old, id]); setBusy(true); setError("")
    try {
      const execution = await mutateAfd<Record<string, unknown>>(`assistant/conversations/${thread}/approvals/${id}`, "POST")
      setMessages(old => [...old, { kind: "execution", result: execution }])
    } catch { setError("Approval expired, was already used, or execution was blocked. Check Activity before retrying.") }
    finally { setBusy(false) }
  }

  return <>
    <Button className="fixed bottom-5 right-5 z-40 gap-2 shadow-lg" onClick={() => setOpen(true)} aria-expanded={open}><Bot className="h-4 w-4" />Desk Assistant</Button>
    {open && <aside aria-label="Desk Assistant" className="fixed inset-y-0 right-0 z-50 flex w-full max-w-lg flex-col border-l bg-background shadow-2xl">
      <header className="flex items-center justify-between border-b p-4"><div><h2 className="font-semibold">Desk Assistant</h2><p className="text-xs text-muted-foreground">{selected?.alias ?? "No account selected"} · {context.market ?? pathname.slice(1)}</p></div><Button variant="ghost" size="icon" aria-label="Close assistant" onClick={() => setOpen(false)}><X /></Button></header>
      <div className="space-y-2 border-b p-4">
        <label className="block text-xs">Mode<select aria-label="Assistant mode" className="mt-1 w-full rounded border bg-background p-2" value={mode} disabled={busy} onChange={e => { setMode(e.target.value); reset() }}><option value="research">Research</option><option value="co_op">Co-op · approval required</option><option value="autonomous">Autonomous · profile permissions</option></select></label>
        <label className="block text-xs">Execution profile<select aria-label="Assistant execution profile" className="mt-1 w-full rounded border bg-background p-2" value={profile} disabled={busy} onChange={e => { setProfile(e.target.value); reset() }}><option value="">{pageProfile ? "Current profile" : "Select a profile"}</option>{profiles.filter(p => !selected || p.account_id === selected.publicRef).map(p => <option key={p.public_id} value={p.public_id}>{p.name}</option>)}</select></label>
        <p className="text-xs text-muted-foreground">{mode === "research" ? "Read-only research. No trading actions." : mode === "co_op" ? "Review the exact preview below before approving. Approvals expire after 60 seconds." : "Execution uses existing demo permissions and safety controls. Selecting this mode does not enable autonomy."}</p>
        <div className="flex gap-2"><select aria-label="Previous conversations" className="min-w-0 flex-1 rounded border bg-background p-2 text-xs" value={threads.some(t => t.id === thread) ? thread : ""} disabled={busy} onChange={e => { if (e.target.value) void restore(e.target.value) }}><option value="">Previous conversations</option>{threads.map(t => <option key={t.id} value={t.id}>{t.mode} · {t.id.slice(-8)}</option>)}</select><Button variant="outline" size="sm" disabled={busy} onClick={reset}>New</Button></div>
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-4" role="log" aria-live="polite">
        {!messages.length && <p className="text-sm text-muted-foreground">Ask about this market, your account, or an execution profile.</p>}
        {messages.map((m, index) => <article key={index} className={`rounded-lg p-3 text-sm ${m.kind === "user" ? "bg-emerald-600/10" : "bg-muted/60"}`}>
          {m.text && <p className="whitespace-pre-wrap break-words">{m.text}</p>}
          {m.result && <><AssistantResult result={m.result} title={m.kind === "execution" ? "Execution result" : m.tool?.replaceAll("_", " ") ?? "Action"}/>
            {m.result.status === "approval_required" && <Button className="mt-3 w-full" disabled={busy || used.includes(String(m.result.approval_id)) || mode !== "co_op"} onClick={() => void approve(m.result!)}>Approve {String(m.result.action).replaceAll("_", " ")}</Button>}</>}
        </article>)}<div ref={scroll} />
      </div>
      {error && <p role="alert" className="border-t p-3 text-sm text-amber-600">{error}</p>}
      <form className="flex gap-2 border-t p-4" onSubmit={e => { e.preventDefault(); void send() }}><textarea aria-label="Message to assistant" value={input} maxLength={8000} onChange={e => setInput(e.target.value)} className="min-h-20 flex-1 resize-none rounded border bg-background p-2 text-sm" placeholder="Ask your desk…" disabled={busy}/>{busy ? <Button type="button" variant="outline" aria-label="Stop response" onClick={() => abort.current?.abort()}><Square className="h-4 w-4"/></Button> : <Button aria-label="Send message" disabled={!input.trim() || (mode !== "research" && !boundProfile)}><Send className="h-4 w-4"/></Button>}</form>
    </aside>}
  </>
}
