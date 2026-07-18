import type { Account, AutonomousControls, AutonomousRun, BrokerStatus, Connection, DailySummary, DemoExecution, ProfileSummary, Schedule, WorkerHealth } from "./contracts"

export type DashboardSection = "status" | "connections" | "accounts" | "profiles" | "runs" | "executions" | "schedules" | "worker" | "daily" | "controls"
export type DashboardRequest = <T>(path: string) => Promise<T>
const outcomes = { TRADE: 0, NO_TRADE: 0, BLOCKED: 0, MARKET_CLOSED: 0, SKIPPED: 0, ERROR: 0 }
export const blockedControls: AutonomousControls = { global_autonomous_kill_switch: true, demo_autonomous_enabled: false, live_autonomous_enabled: false, live_execution_supported: false, updated_at: "", effective: { demo: "blocked", live: "blocked" } }

export interface DashboardData { status: BrokerStatus | null; connections: Connection[]; accounts: Account[]; profiles: ProfileSummary[]; runs: AutonomousRun[]; executions: DemoExecution[]; schedules: Schedule[]; worker: WorkerHealth; daily: DailySummary; controls: AutonomousControls; errors: Partial<Record<DashboardSection, string>>; coreUnavailable: boolean }

function failure(reason: unknown) { return reason && typeof reason === "object" && "category" in reason ? String((reason as { category: unknown }).category) : "unavailable" }

export async function loadDashboardData(request: DashboardRequest): Promise<DashboardData> {
  const calls = [
    ["status", "/api/broker/status"], ["connections", "/api/broker/connections"], ["accounts", "/api/broker/accounts"],
    ["profiles", "/api/execution-profiles"], ["runs", "/api/autonomous-runs"], ["executions", "/api/demo-executions"],
    ["schedules", "/api/autonomous-schedules"], ["worker", "/api/autonomous-worker-health"],
    ["daily", "/api/autonomous-daily-summary"], ["controls", "/api/autonomous-controls"],
  ] as const
  const settled = await Promise.allSettled(calls.map(([, path]) => request<unknown>(path)))
  const values: Partial<Record<DashboardSection, unknown>> = {}
  const errors: Partial<Record<DashboardSection, string>> = {}
  settled.forEach((result, index) => result.status === "fulfilled" ? values[calls[index][0]] = result.value : errors[calls[index][0]] = failure(result.reason))
  const controls = values.controls as Partial<AutonomousControls> | undefined
  const normalizedControls: AutonomousControls = controls && typeof controls.global_autonomous_kill_switch === "boolean" && typeof controls.demo_autonomous_enabled === "boolean" && typeof controls.live_autonomous_enabled === "boolean"
    ? { ...blockedControls, ...controls, effective: { ...blockedControls.effective, ...controls.effective } }
    : blockedControls
  const workerValue = values.worker as Partial<WorkerHealth> | undefined
  const normalizedWorker: WorkerHealth = workerValue && typeof workerValue.status === "string" && Array.isArray(workerValue.workers) ? workerValue as WorkerHealth : { status: "unavailable", workers: [] }
  const dailyValue = values.daily as Partial<DailySummary> | undefined
  const normalizedDaily: DailySummary = dailyValue && dailyValue.outcomes && typeof dailyValue.outcomes === "object"
    ? { date: dailyValue.date ?? "", outcomes: { ...outcomes, ...dailyValue.outcomes }, daily_entry_count: dailyValue.daily_entry_count ?? 0, kill_switch: dailyValue.kill_switch ?? true, armed_profiles: dailyValue.armed_profiles ?? 0 }
    : { date: "", outcomes, daily_entry_count: 0, kill_switch: true, armed_profiles: 0 }
  return {
    status: values.status as BrokerStatus ?? null,
    connections: (values.connections as { connections?: Connection[] } | undefined)?.connections ?? [],
    accounts: (values.accounts as { accounts?: Account[] } | undefined)?.accounts ?? [],
    profiles: (values.profiles as { profiles?: ProfileSummary[] } | undefined)?.profiles ?? [],
    runs: (values.runs as { runs?: AutonomousRun[] } | undefined)?.runs ?? [],
    executions: (values.executions as { executions?: DemoExecution[] } | undefined)?.executions ?? [],
    schedules: (values.schedules as { schedules?: Schedule[] } | undefined)?.schedules ?? [],
    worker: normalizedWorker,
    daily: normalizedDaily,
    controls: normalizedControls,
    errors, coreUnavailable: Boolean(errors.status && errors.connections),
  }
}
