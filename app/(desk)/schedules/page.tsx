import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { ProfileSummary, Schedule, WorkerHealth } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { ScheduleManager } from "./schedule-manager"

export const metadata = { title: "Schedules" }
export default async function SchedulesPage() { let schedules: Schedule[] = []; let profiles: ProfileSummary[] = []; let worker: WorkerHealth = { status: "unavailable", workers: [] }; try { const request = await authenticatedBackendClient(); const [s, p, w] = await Promise.allSettled([request<{ schedules: Schedule[] }>("/api/autonomous-schedules"), request<{ profiles: ProfileSummary[] }>("/api/execution-profiles"), request<WorkerHealth>("/api/autonomous-worker-health")]); if (s.status === "fulfilled") schedules = s.value.schedules ?? []; if (p.status === "fulfilled") profiles = p.value.profiles ?? []; if (w.status === "fulfilled") worker = w.value } catch { /* safe unavailable defaults */ } return <div className="space-y-7"><PageHeader eyebrow="Durable automation" title="Schedules" description="The persistent backend worker owns dispatch, lateness handling, and safe retries. The web client never launches workers." /><ScheduleManager initialSchedules={schedules} profiles={profiles} worker={worker} /></div> }
