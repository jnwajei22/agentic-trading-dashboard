import type { ExecutionProfileV2 } from "./contracts"

export type ProfilePatch = Partial<ExecutionProfileV2>

export function validateProfile(profile: ExecutionProfileV2) {
  const errors: Record<string, string> = {}
  if (profile.trading_policy.mode === "preset" && !profile.trading_policy.preset_id) errors["trading_policy.preset_id"] = "Select a preset."
  if (profile.market_universe.mode === "groups" && !profile.market_universe.groups.length) errors["market_universe.groups"] = "Select at least one market group."
  if (profile.market_universe.mode === "custom" && !profile.market_universe.included_instrument_ids.length) errors["market_universe.included_instrument_ids"] = "Add at least one instrument."
  const risk = profile.risk_policy
  if (!(risk.minimum_risk_pct <= risk.base_risk_pct && risk.base_risk_pct <= risk.maximum_risk_pct)) errors["risk_policy.base_risk_pct"] = "Minimum ≤ base ≤ maximum is required."
  if (profile.capital_allocation.mode === "fixed_amount" && !profile.capital_allocation.fixed_amount) errors["capital_allocation.fixed_amount"] = "Enter an allocated amount."
  if (profile.capital_allocation.mode === "equity_percentage" && !profile.capital_allocation.equity_percentage) errors["capital_allocation.equity_percentage"] = "Enter an equity percentage."
  if (profile.exit_policy.take_profit.target_reward_to_risk < profile.exit_policy.take_profit.minimum_reward_to_risk) errors["exit_policy.take_profit.target_reward_to_risk"] = "Target must be at least the minimum."
  return errors
}

export function buildProfilePatch(original: ExecutionProfileV2, current: ExecutionProfileV2): ProfilePatch {
  const result: ProfilePatch = {}
  for (const key of Object.keys(current) as Array<keyof ExecutionProfileV2>) {
    if (JSON.stringify(original[key]) !== JSON.stringify(current[key])) Object.assign(result, { [key]: current[key] })
  }
  return result
}
