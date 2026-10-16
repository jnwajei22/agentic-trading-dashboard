export type AssistantEvent = { type: string; text?: string; tool?: string; conversation_id?: string; code?: string; result?: Record<string, unknown> }

export async function consumeAssistantStream(body: ReadableStream<Uint8Array>, receive: (event: AssistantEvent) => void) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let pending = ""
  try {
    while (true) {
      const { value, done } = await reader.read()
      pending += decoder.decode(value, { stream: !done })
      let end: number
      while ((end = pending.indexOf("\n\n")) >= 0) {
        const frame = pending.slice(0, end); pending = pending.slice(end + 2)
        if (frame.startsWith("data: ")) receive(JSON.parse(frame.slice(6)) as AssistantEvent)
      }
      if (done) break
    }
    if (pending.trim()) throw new Error("Assistant stream interrupted.")
  } finally { reader.releaseLock() }
}
