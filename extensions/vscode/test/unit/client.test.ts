import assert from "node:assert/strict";
import { test } from "node:test";
import { BridgeClient, BridgeHttpError, isAbortError, isAuthError, isConnectionError } from "../../src/client.ts";
import type { Fetch } from "../../src/client.ts";

interface Call {
  url: string;
  init: RequestInit | undefined;
}

function fakeFetch(respond: (call: Call) => Response | Promise<Response>): { fetch: Fetch; calls: Call[] } {
  const calls: Call[] = [];
  const fetchImpl = (async (url: string | URL | Request, init?: RequestInit) => {
    const call = { url: String(url), init };
    calls.push(call);
    return respond(call);
  }) as Fetch;
  return { fetch: fetchImpl, calls };
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

test("complete sends the FIM prompt and returns the text", async () => {
  const { fetch, calls } = fakeFetch(() => json(200, { choices: [{ text: "(fullName);" }] }));
  const client = new BridgeClient(11435, fetch);
  const signal = new AbortController().signal;
  assert.equal(await client.complete("claude-haiku", "<fim_prefix>x<fim_suffix><fim_middle>", signal), "(fullName);");
  assert.equal(calls[0].url, "http://127.0.0.1:11435/v1/completions");
  assert.equal(calls[0].init?.method, "POST");
  assert.equal(calls[0].init?.signal, signal);
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    model: "claude-haiku",
    prompt: "<fim_prefix>x<fim_suffix><fim_middle>",
    stream: false,
  });
});

test("complete returns an empty string when the reply has no text", async () => {
  const { fetch } = fakeFetch(() => json(200, { choices: [] }));
  assert.equal(await new BridgeClient(1, fetch).complete("m", "p", new AbortController().signal), "");
});

test("complete throws the bridge error message", async () => {
  const { fetch } = fakeFetch(() => json(500, { error: { message: "No 'claude' worker could start" } }));
  await assert.rejects(
    new BridgeClient(1, fetch).complete("m", "p", new AbortController().signal),
    (error: unknown) => error instanceof BridgeHttpError && error.status === 500 && error.message.includes("worker"),
  );
});

test("a sign-in failure is an auth error, other bridge errors are not", async () => {
  const signedOut = fakeFetch(() => json(401, { error: { message: "The claude CLI is not signed in", type: "authentication_failed" } }));
  const error = await new BridgeClient(1, signedOut.fetch).complete("m", "p", new AbortController().signal).catch((caught: unknown) => caught);
  assert.ok(error instanceof BridgeHttpError);
  assert.equal(error.type, "authentication_failed");
  assert.equal(isAuthError(error), true);
  assert.equal(isAuthError(new BridgeHttpError(502, "Rate limited", "rate_limit")), false);
  assert.equal(isAuthError(new TypeError("fetch failed")), false);
});

test("health returns the status or undefined", async () => {
  const healthy = fakeFetch(() => json(200, { status: "ok", managed: true, leases: 2 }));
  assert.deepEqual(await new BridgeClient(1, healthy.fetch).health(), { status: "ok", managed: true, leases: 2 });
  const refused = fakeFetch(() => Promise.reject(new TypeError("fetch failed")));
  assert.equal(await new BridgeClient(1, refused.fetch).health(), undefined);
  const wrong = fakeFetch(() => json(200, { hello: "world" }));
  assert.equal(await new BridgeClient(1, wrong.fetch).health(), undefined);
});

test("lease calls use the client id", async () => {
  const { fetch, calls } = fakeFetch(() => json(200, { leases: 1 }));
  const client = new BridgeClient(9, fetch);
  assert.equal(await client.renewLease("window a"), true);
  await client.releaseLease("window a");
  assert.equal(calls[0].url, "http://127.0.0.1:9/lease");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), { client: "window a" });
  assert.equal(calls[1].url, "http://127.0.0.1:9/lease/window%20a");
  assert.equal(calls[1].init?.method, "DELETE");
});

test("renewLease returns false when the bridge is down", async () => {
  const { fetch } = fakeFetch(() => Promise.reject(new TypeError("fetch failed")));
  assert.equal(await new BridgeClient(1, fetch).renewLease("w"), false);
});

test("error classification", () => {
  const abort = new DOMException("aborted", "AbortError");
  assert.equal(isAbortError(abort), true);
  assert.equal(isConnectionError(new TypeError("fetch failed")), true);
  assert.equal(isConnectionError(new BridgeHttpError(500, "x")), false);
});
