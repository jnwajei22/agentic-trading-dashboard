import type { Account,AutonomousControls,BrokerStatus,ProfileSummary,Schedule,WorkerHealth } from "@/lib/afd/contracts"
export type AutomationState="Ready"|"Paused"|"Blocked by Kill Switch"|"No Enabled Strategies"|"No Schedule"|"Broker Disconnected"|"Needs Attention"
export function automationStatus(input:{status:BrokerStatus|null;accounts:Account[];profiles:ProfileSummary[];schedules:Schedule[];controls:AutonomousControls;worker:WorkerHealth}):{state:AutomationState;message:string}{
  if(!input.status?.connected)return {state:"Broker Disconnected",message:"Add or reauthenticate a trading connection."}
  if(input.controls.global_autonomous_kill_switch)return {state:"Blocked by Kill Switch",message:"Scheduled submissions are blocked by the global safety control."}
  if(!input.profiles.some(profile=>profile.enabled))return {state:"No Enabled Strategies",message:"Create or enable a strategy to begin automation."}
  if(!input.schedules.some(schedule=>schedule.enabled))return {state:"No Schedule",message:"Create a schedule for an enabled strategy."}
  if(input.worker.status!=="healthy")return {state:"Needs Attention",message:"Automation services cannot currently be verified."}
  if(!input.controls.demo_autonomous_enabled)return {state:"Paused",message:"Demo autonomous trading is paused."}
  return {state:"Ready",message:"Scheduled strategies can run automatically."}
}
