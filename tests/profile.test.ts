import test from "node:test"
import assert from "node:assert/strict"
import { buildProfilePatch, validateProfile } from "../lib/afd/profile"
import type { ExecutionProfileV2 } from "../lib/afd/contracts"

const profile = { schema_version: 2, trading_policy: { mode: "adaptive", preset_id: null, decision_interval: "scheduled", minimum_confidence: .7 }, market_universe: { mode: "all_available", groups: [], included_instrument_ids: [], excluded_instrument_ids: [] }, risk_policy: { mode: "fixed", fixed_risk_pct: .25, base_risk_pct: .25, minimum_risk_pct: .1, maximum_risk_pct: .5, maximum_total_open_risk_pct: 1, maximum_margin_utilization_pct: null, maximum_correlated_risk_pct: null, daily_loss_limit_pct: 3, drawdown_cutoff_pct: 10, maximum_open_positions: 3, maximum_pending_entry_orders: 1, maximum_new_entries_per_day: 2 }, capital_allocation: { mode: "full_account", fixed_amount: null, equity_percentage: null, risk_base: "account_equity", compounding_mode: "disabled", maximum_margin_utilization_pct: 70, maximum_gross_exposure_multiple: null, allow_shared_capital: false }, exit_policy: { stop_loss: { enabled: true, mode: "adaptive_structure", fixed_distance: null, fixed_percentage: null, maximum_risk_distance: null }, take_profit: { enabled: true, mode: "reward_to_risk", minimum_reward_to_risk: 1.5, target_reward_to_risk: 2 }, trailing_stop: { enabled: false, activation_mode: "reward_multiple", activation_value: null, trail_value: null }, break_even: { enabled: false, activation_reward_multiple: 1, offset: 0 }, partial_exits: [] }, schedule_policy: { timezone: "America/Chicago", times: [], market_aware: true, run_when_any_selected_market_is_open: true }, enabled: true } satisfies ExecutionProfileV2

test("profile patch contains changed top-level sections only", () => {
  const changed = structuredClone(profile); changed.trading_policy.minimum_confidence = .8
  assert.deepEqual(Object.keys(buildProfilePatch(profile, changed)), ["trading_policy"])
})
test("conditional profile validation blocks incomplete custom universe", () => {
  const changed: ExecutionProfileV2 = structuredClone(profile); changed.market_universe.mode = "custom"
  assert.match(validateProfile(changed)["market_universe.included_instrument_ids"], /instrument/)
})
