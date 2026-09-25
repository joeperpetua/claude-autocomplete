export type Fetch = typeof fetch;

export interface Health {
  status: string;
  managed: boolean;
  leases: number;
}

interface CompletionPayload {
  choices?: { text?: unknown }[];
  error?: { message?: string };
}

export class BridgeHttpError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "BridgeHttpError";
    this.status = status;
  }
}

export function isAbortError(error: unknown): boolean {
  return error instanceof Error && (error.name === "AbortError" || error.name === "TimeoutError");
}

export function isConnectionError(error: unknown): boolean {
  return error instanceof TypeError && !isAbortError(error);
}

export class BridgeClient {
  readonly baseUrl: string;
  private readonly fetchImpl: Fetch;

  constructor(port: number, fetchImpl: Fetch = fetch) {
    this.baseUrl = `http://127.0.0.1:${port}`;
    this.fetchImpl = fetchImpl;
  }

  async complete(model: string, prompt: string, signal: AbortSignal): Promise<string> {
    const response = await this.fetchImpl(`${this.baseUrl}/v1/completions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model, prompt, stream: false }),
      signal,
    });
    const payload = (await response.json().catch(() => undefined)) as CompletionPayload | undefined;
    if (!response.ok) {
      throw new BridgeHttpError(response.status, payload?.error?.message ?? `HTTP ${response.status}`);
    }
    const text = payload?.choices?.[0]?.text;
    return typeof text === "string" ? text : "";
  }

  async health(timeoutMs = 1000): Promise<Health | undefined> {
    try {
      const response = await this.fetchImpl(`${this.baseUrl}/`, { signal: AbortSignal.timeout(timeoutMs) });
      if (!response.ok) {
        return undefined;
      }
      const payload = (await response.json()) as Partial<Health> | undefined;
      return payload?.status === "ok" ? { status: "ok", managed: payload.managed === true, leases: Number(payload.leases ?? 0) } : undefined;
    } catch {
      return undefined;
    }
  }

  async renewLease(client: string, timeoutMs = 2000): Promise<boolean> {
    try {
      const response = await this.fetchImpl(`${this.baseUrl}/lease`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ client }),
        signal: AbortSignal.timeout(timeoutMs),
      });
      return response.ok;
    } catch {
      return false;
    }
  }

  async releaseLease(client: string, timeoutMs = 1000): Promise<void> {
    try {
      await this.fetchImpl(`${this.baseUrl}/lease/${encodeURIComponent(client)}`, {
        method: "DELETE",
        signal: AbortSignal.timeout(timeoutMs),
      });
    } catch {
      return;
    }
  }
}
