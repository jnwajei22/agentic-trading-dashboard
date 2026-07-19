import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { ProfileSummary, Schedule } from "@/lib/afd/contracts"
import { ScheduleManager } from "./schedule-manager"
export const metadata={title:"Schedules"}
export default async function SchedulesPage(){let schedules:Schedule[]=[];let profiles:ProfileSummary[]=[];try{const request=await authenticatedBackendClient();const [s,p]=await Promise.all([request<{schedules:Schedule[]}>("/api/autonomous-schedules"),request<{profiles:ProfileSummary[]}>("/api/execution-profiles")]);schedules=s.schedules??[];profiles=p.profiles??[]}catch{}return <ScheduleManager initialSchedules={schedules} profiles={profiles}/>}
