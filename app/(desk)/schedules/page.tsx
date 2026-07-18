import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { ProfileSummary, Schedule } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { ScheduleManager } from "./schedule-manager"
export const metadata={title:"Schedules"}
export default async function SchedulesPage(){let schedules:Schedule[]=[];let profiles:ProfileSummary[]=[];try{const request=await authenticatedBackendClient();const [s,p]=await Promise.all([request<{schedules:Schedule[]}>("/api/autonomous-schedules"),request<{profiles:ProfileSummary[]}>("/api/execution-profiles")]);schedules=s.schedules??[];profiles=p.profiles??[]}catch{}return <div className="space-y-7"><PageHeader eyebrow="Durable automation" title="Schedules" description="Build market-aware schedules, preview upcoming runs, and pause or resume them without cron syntax."/><ScheduleManager initialSchedules={schedules} profiles={profiles}/></div>}
