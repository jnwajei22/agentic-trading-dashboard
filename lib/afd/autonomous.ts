import type { AutonomousControls } from "./contracts"

export async function updateControlsWithRollback(
  previous: AutonomousControls,
  patch: object,
  set: (value: AutonomousControls) => void,
  request: (path: string, method: "PATCH", body: object) => Promise<AutonomousControls>,
) {
  try {
    const confirmed = await request("autonomous-controls", "PATCH", patch)
    set(confirmed)
    return confirmed
  } catch (error) {
    set(previous)
    throw error
  }
}
