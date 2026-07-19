import {authenticatedBackendClient} from "@/lib/afd/backend"
import type {Account,ActivityEvent,ProfileSummary} from "@/lib/afd/contracts"
import {ActivityWorkspace} from "./workspace"

export const metadata={title:"Activity"}
export default async function ActivityPage(){
  let events:ActivityEvent[]=[];let accounts:Account[]=[];let profiles:ProfileSummary[]=[];let loadError=false
  try{const request=await authenticatedBackendClient();const [activity,accountData,profileData]=await Promise.allSettled([request<{events:ActivityEvent[]}>("/api/activity"),request<{accounts:Account[]}>("/api/broker/accounts"),request<{profiles:ProfileSummary[]}>("/api/execution-profiles")]);if(activity.status==="fulfilled")events=activity.value.events??[];else loadError=true;if(accountData.status==="fulfilled")accounts=accountData.value.accounts??[];if(profileData.status==="fulfilled")profiles=profileData.value.profiles??[]}catch{loadError=true}
  return <ActivityWorkspace initialEvents={events} accounts={accounts} profiles={profiles} initialError={loadError}/>
}
