import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { AutonomousRun, DemoExecution } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { ActivityWorkspace } from "./workspace"

export const metadata = { title: "Activity & audit" }
export default async function ActivityPage() { let runs: AutonomousRun[] = []; let executions: DemoExecution[] = []; let controls: Array<Record<string, unknown>> = []; try { const request = await authenticatedBackendClient(); const [r, e, c] = await Promise.allSettled([request<{ runs: AutonomousRun[] }>("/api/autonomous-runs"), request<{ executions: DemoExecution[] }>("/api/demo-executions"), request<{ events: Array<Record<string, unknown>> }>("/api/autonomous-controls/audit")]); if (r.status === "fulfilled") runs = r.value.runs ?? []; if (e.status === "fulfilled") executions = e.value.executions ?? []; if (c.status === "fulfilled") controls = c.value.events ?? [] } catch { /* empty audit state */ } return <div className="space-y-7"><PageHeader eyebrow="Structured audit" title="Activity" description="Autonomous outcomes, demo execution records, usage summaries, and safety-control history returned by the backend. Private reasoning is never displayed." /><ActivityWorkspace runs={runs} executions={executions} controlEvents={controls} /></div> }
