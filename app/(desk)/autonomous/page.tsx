import { authenticatedBackendClient } from "@/lib/afd/backend"
import { blockedControls } from "@/lib/afd/dashboard-data"
import type { Account, AutonomousControls, AutonomousRun, ProfileSummary, Schedule } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { AutonomousWorkspace } from "./workspace"

export const metadata = { title: "Autonomous trading" }
export default async function AutonomousPage() {
  let controls: AutonomousControls = blockedControls; let accounts: Account[] = []; let profiles: ProfileSummary[] = []; let schedules: Schedule[] = []; let runs: AutonomousRun[] = []; let degraded = false
  try { const request = await authenticatedBackendClient(); const settled = await Promise.allSettled([request<AutonomousControls>("/api/autonomous-controls"), request<{ accounts: Account[] }>("/api/broker/accounts"), request<{ profiles: ProfileSummary[] }>("/api/execution-profiles"), request<{ schedules: Schedule[] }>("/api/autonomous-schedules"), request<{ runs: AutonomousRun[] }>("/api/autonomous-runs")]); if (settled[0].status === "fulfilled") controls = settled[0].value; if (settled[1].status === "fulfilled") accounts = settled[1].value.accounts ?? []; if (settled[2].status === "fulfilled") profiles = settled[2].value.profiles ?? []; if (settled[3].status === "fulfilled") schedules = settled[3].value.schedules ?? []; if (settled[4].status === "fulfilled") runs = settled[4].value.runs ?? []; degraded = settled.some((r) => r.status === "rejected") } catch { degraded = true }
  return <div className="space-y-7"><PageHeader eyebrow="Safety & automation" title="Autonomous trading" description="Backend-enforced global controls, account-bound execution profiles, schedules, capability verification, and recent decisions." /><AutonomousWorkspace initialControls={controls} accounts={accounts} profiles={profiles} schedules={schedules} runs={runs} degraded={degraded} /></div>
}
