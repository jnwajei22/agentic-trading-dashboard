"use client"
import { FormEvent, useState } from "react"
import { useRouter } from "next/navigation"
import { ShieldCheck } from "lucide-react"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { browserAfdFetch, BrowserAfdError } from "@/lib/afd/browser-client"

export function BrokerConnectForm({ connectionId, createNew }: { connectionId?: string; createNew: boolean }) {
  const router = useRouter(); const [busy, setBusy] = useState(false); const [error, setError] = useState("")
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setBusy(true); setError("")
    const data = new FormData(event.currentTarget)
    try {
      await browserAfdFetch("broker/tradelocker/save-credentials", { method: "POST", body: JSON.stringify({ username: data.get("username"), password: data.get("password"), server: data.get("server"), environment: data.get("environment"), label: data.get("label") || undefined, connection_id: connectionId, create_new: createNew }) })
      router.replace(`/select-account${connectionId ? `?connection_id=${encodeURIComponent(connectionId)}` : ""}`)
    } catch (caught) { setError(caught instanceof BrowserAfdError ? caught.message : "TradeLocker could not be reached.") } finally { setBusy(false) }
  }
  return <section className="w-full max-w-lg rounded-2xl border bg-card p-6 shadow-sm sm:p-8"><div className="flex gap-4"><span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-emerald-600 text-white"><ShieldCheck /></span><div><p className="text-xs font-semibold uppercase tracking-[.18em] text-emerald-600">Broker onboarding</p><h1 className="mt-1 text-2xl font-semibold">{connectionId ? "Reauthenticate" : "Connect TradeLocker"}</h1><p className="mt-2 text-sm text-muted-foreground">Credentials pass through the encrypted same-origin server route and are stored only by the existing backend.</p></div></div><form onSubmit={submit} className="mt-7 space-y-4">{error && <p role="alert" className="rounded-lg border border-red-500/30 bg-red-500/10 p-3 text-sm text-red-700 dark:text-red-300">{error}</p>}<Field label="Connection label" name="label" autoComplete="off" placeholder="Primary demo" /><Field label="TradeLocker username" name="username" type="email" autoComplete="username" required /><Field label="TradeLocker password" name="password" type="password" autoComplete="current-password" required /><Field label="Server" name="server" autoComplete="off" placeholder="TradeLocker server" required /><div><Label htmlFor="environment">Environment</Label><select id="environment" name="environment" className="mt-1.5 h-10 w-full rounded-md border bg-background px-3 text-sm"><option value="demo">Demo</option><option value="live">Live</option></select></div><div className="flex gap-3 pt-2"><Button type="button" variant="outline" onClick={() => router.back()}>Cancel</Button><Button disabled={busy} className="flex-1">{busy ? "Verifying securely…" : "Verify and discover accounts"}</Button></div></form></section>
}
function Field(props: React.ComponentProps<typeof Input> & { label: string }) { const { label, ...input } = props; return <div><Label htmlFor={String(input.name)}>{label}</Label><Input id={String(input.name)} className="mt-1.5" {...input} /></div> }
