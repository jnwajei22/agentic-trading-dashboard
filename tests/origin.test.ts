import test from "node:test"
import assert from "node:assert/strict"
import { isSameOriginRequest } from "../lib/afd/origin"

test("same-origin uses incoming host and requires exact scheme, port and origin", () => {
  const request = (origin?: string, host = "127.0.0.1:3102", protocol = "http:") => ({ headers: new Headers({ host, ...(origin ? { origin } : {}) }), nextUrl: { protocol } })
  assert.ok(isSameOriginRequest(request("http://127.0.0.1:3102")))
  assert.ok(isSameOriginRequest(request("https://desk.example", "desk.example", "https:")))
  for (const origin of [undefined, "null", "https://foreign.example", "http://127.0.0.1:3103", "https://127.0.0.1:3102", "http://127.0.0.1:3102/path"]) assert.ok(!isSameOriginRequest(request(origin)))
})
