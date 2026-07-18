import type { Account } from "@/lib/afd/contracts"

export interface GlobalAccountContext {
  publicRef:string; alias:string; accountNumber:string; broker:string; platform:string;
  environment:string; currency:string; available:boolean; isDefault:boolean; nickname?:string
}
export function normalizeAccountContext(account:Account):GlobalAccountContext{return {
  publicRef:account.public_id,alias:account.account_alias,accountNumber:account.account_number||account.account_alias,
  broker:account.broker_name||"Broker",platform:account.platform_name||"TradeLocker",environment:account.environment,
  currency:account.currency||"Currency unavailable",available:account.available&&account.locally_enabled,
  isDefault:account.is_default_analysis,nickname:account.nickname,
}}
export function selectedAccount(accounts:Account[]){return accounts.find(account=>account.is_default_analysis)??null}
