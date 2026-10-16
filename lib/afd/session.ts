// Presentation only: there is no browser login or authenticated session.
export async function workspaceSession() {
  return { user: { name: "Shared workspace", email: null } }
}
