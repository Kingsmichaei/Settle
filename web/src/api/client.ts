import type {
  AdminSummary,
  Booking,
  BookingSummary,
  CancellationQuote,
  Checkout,
  Dispute,
  HostSummary,
  Listing,
  OutboxEvent,
  ReconciliationRun,
  Token,
  User,
} from "./types";

const TOKEN_KEY = "settle.token";

/** An error the API explained (RFC 9457 problem details) or a network failure. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

export const tokenStore = {
  get: (): string | null => localStorage.getItem(TOKEN_KEY),
  set: (token: string) => localStorage.setItem(TOKEN_KEY, token),
  clear: () => localStorage.removeItem(TOKEN_KEY),
};

type Method = "GET" | "POST";

async function request<T>(method: Method, path: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {};
  const token = tokenStore.get();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (body !== undefined) headers["Content-Type"] = "application/json";
  // Every write carries a fresh idempotency key, so a retried click can't act twice.
  if (method === "POST") headers["Idempotency-Key"] = crypto.randomUUID();

  let response: Response;
  try {
    response = await fetch(path, {
      method,
      headers,
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch {
    throw new ApiError(0, "network", "Can't reach Settle. Check that the API is running.");
  }
  if (response.status === 401) tokenStore.clear();
  if (!response.ok) {
    const problem = await response.json().catch(() => null);
    const detail =
      typeof problem?.detail === "string"
        ? problem.detail
        : Array.isArray(problem?.detail)
          ? problem.detail.map((d: { msg: string }) => d.msg).join("; ")
          : response.statusText;
    throw new ApiError(response.status, problem?.type ?? "error", detail);
  }
  return (await response.json()) as T;
}

const get = <T>(path: string) => request<T>("GET", path);
const post = <T>(path: string, body?: unknown) => request<T>("POST", path, body);

export const api = {
  login: (email: string, password: string) => post<Token>("/v1/auth/token", { email, password }),
  me: () => get<User>("/v1/me"),

  listings: () => get<Listing[]>("/v1/listings"),
  listing: (id: string) => get<Listing>(`/v1/listings/${id}`),

  bookings: () => get<BookingSummary[]>("/v1/bookings"),
  booking: (id: string) => get<Booking>(`/v1/bookings/${id}`),
  book: (listingId: string, checkIn: string, checkOut: string) =>
    post<Booking>("/v1/bookings", { listing_id: listingId, check_in: checkIn, check_out: checkOut }),
  checkout: (id: string) => post<Checkout>(`/v1/bookings/${id}/checkout`),
  confirm: (id: string) => post<Booking>(`/v1/bookings/${id}/confirm`),
  cancellationQuote: (id: string) => get<CancellationQuote>(`/v1/bookings/${id}/cancellation-quote`),
  cancel: (id: string, reason: string) => post<Booking>(`/v1/bookings/${id}/cancel`, { reason }),

  openDispute: (bookingId: string, reason: string, claimMinor: number, currency: string, statement: string) =>
    post<Dispute>(`/v1/bookings/${bookingId}/disputes`, {
      reason,
      claim: { amount_minor: claimMinor, currency },
      statement: statement || null,
    }),
  dispute: (id: string) => get<Dispute>(`/v1/disputes/${id}`),
  addEvidence: (id: string, kind: "statement" | "photo", content: string, url?: string) =>
    post<Dispute>(`/v1/disputes/${id}/evidence`, { kind, content, url: url || null }),

  hostSummary: () => get<HostSummary>("/v1/hosts/me/summary"),
  askAssistant: (question: string) =>
    post<{ answer: string; answered_by: string }>("/v1/hosts/me/assistant", { question }),

  admin: {
    summary: () => get<AdminSummary>("/v1/admin/summary"),
    bookings: () => get<BookingSummary[]>("/v1/admin/bookings"),
    riskQueue: () => get<BookingSummary[]>("/v1/admin/risk"),
    disputes: () => get<Dispute[]>("/v1/admin/disputes"),
    recommend: (id: string) => post<Dispute>(`/v1/admin/disputes/${id}/recommend`),
    decide: (id: string, refundMinor: number, currency: string, note: string) =>
      post<Dispute>(`/v1/admin/disputes/${id}/decide`, {
        refund: { amount_minor: refundMinor, currency },
        note,
      }),
    approvePayout: (bookingId: string) => post<Booking>(`/v1/admin/bookings/${bookingId}/approve-payout`),
    retryPayout: (bookingId: string) => post<Booking>(`/v1/admin/bookings/${bookingId}/retry-payout`),
    deadEvents: () => get<OutboxEvent[]>("/v1/admin/outbox"),
    reconciliationRuns: () => get<ReconciliationRun[]>("/v1/admin/reconciliation/runs"),
    reconcileNow: () => post<ReconciliationRun>("/v1/admin/reconciliation/runs"),
  },
};
