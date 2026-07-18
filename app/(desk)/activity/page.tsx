import { authenticatedBackendClient } from "@/lib/afd/backend"
import type { Account,ActivityEvent,ProfileSummary } from "@/lib/afd/contracts"
import { PageHeader } from "@/components/desk/page-header"
import { ActivityWorkspace } from "./workspace"
export const metadata={title:"Activity"}
export default async function ActivityPage(){let events:ActivityEvent[]=[];let accounts:Account[]=[];let profiles:ProfileSummary[]=[];try{const request=await authenticatedBackendClient();const [activity,accountData,profileData]=await Promise.all([request<{events:ActivityEvent[]}>("/api/activity"),request<{accounts:Account[]}>("/api/broker/accounts"),request<{profiles:ProfileSummary[]}>("/api/execution-profiles")]);events=activity.events??[];accounts=accountData.accounts??[];profiles=profileData.profiles??[]}catch{}return <div className="space-y-7"><PageHeader eyebrow="Structured audit" title="Activity" description="Filter and export backend-confirmed strategy, demo execution, and safety events. Private reasoning is never displayed."/><ActivityWorkspace initialEvents={events} accounts={accounts} profiles={profiles}/></div>}
