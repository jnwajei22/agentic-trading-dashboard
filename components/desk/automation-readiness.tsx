import Link from "next/link"
import { Check, ArrowUpRight } from "lucide-react"
import type { AutonomousRun, ProfileSummary, Schedule, WorkerHealth } from "@/lib/afd/contracts"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"

export function AutomationReadiness({ worker, profiles, schedules, runs }: {
  worker: WorkerHealth; profiles: ProfileSummary[]; schedules: Schedule[]; runs: AutonomousRun[];
}) {
  const healthy = worker.status === "healthy" && worker.workers.some(item => item.healthy)
  const enabled = new Set(profiles.filter(profile => profile.enabled).map(profile => profile.public_id))
  const steps = [
    {label:"Configure a strategy", detail:"Choose its markets, risk limits, and permissions.", href:profiles[0]?`/autonomous/${profiles[0].public_id}`:"/autonomous?create=1", done:profiles.length > 0},
    {label:"Test the strategy", detail:"Run analysis without submitting an order.", href:"/autonomous", done:runs.some(run => run.dry_run && run.outcome !== "ERROR")},
    {label:"Set a schedule", detail:"Save when the enabled strategy should be evaluated.", href:"/schedules?create=1", done:schedules.some(schedule => schedule.enabled && enabled.has(schedule.profile_ref))},
  ]
  return <Card className="overflow-hidden border-border/50">
    <CardHeader className="gap-2"><div className="flex flex-wrap items-center justify-between gap-3"><CardTitle>Automation readiness</CardTitle><span className={`rounded-full px-3 py-1 text-xs ${healthy ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "bg-muted text-muted-foreground"}`}>{healthy ? "Worker available" : "Worker not connected"}</span></div><p className="max-w-2xl text-xs leading-relaxed text-muted-foreground">{healthy ? "A worker is checking for scheduled runs. Each run still has to pass your permissions, provider, and risk checks." : "Scheduled runs need an available worker. You can configure strategies, run manual analysis, and save schedules now."}</p></CardHeader>
    <CardContent className="grid gap-3 md:grid-cols-3">{steps.map((step,index) => <Link href={step.href} key={step.label} className="group rounded-2xl bg-muted/30 p-4 transition-colors hover:bg-muted/60 focus-visible:outline focus-visible:outline-2 focus-visible:outline-ring"><div className="flex items-center justify-between"><span className="flex h-6 w-6 items-center justify-center rounded-full bg-background text-xs text-muted-foreground">{step.done ? <Check className="h-3.5 w-3.5" aria-label="Completed"/> : index+1}</span><ArrowUpRight className="h-3.5 w-3.5 text-muted-foreground" aria-hidden="true"/></div><p className="mt-3 text-sm font-medium">{step.label}</p><p className="mt-1 text-xs leading-relaxed text-muted-foreground">{step.detail}</p></Link>)}</CardContent>
  </Card>
}
