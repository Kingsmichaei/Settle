// Mirrors settle/api/schemas.py. Keep the two in step when the API changes.

export type Role = "guest" | "host" | "admin";

export type BookingStatus =
  | "pending"
  | "authorized"
  | "captured"
  | "held"
  | "disputed"
  | "partially_refunded"
  | "released"
  | "paid_out"
  | "payout_failed"
  | "voided"
  | "refunded";

export interface Money {
  amount_minor: number;
  currency: string;
  formatted: string;
}

export interface User {
  id: string;
  email: string;
  full_name: string;
  role: Role;
  country: string;
  default_currency: string;
}

export interface Token {
  access_token: string;
  expires_at: string;
  user: User;
}

export interface Listing {
  id: string;
  title: string;
  city: string;
  country: string;
  image_url: string | null;
  nightly_price: Money;
  cancellation_policy: "flexible" | "moderate" | "strict";
  policy_summary: string;
  host_name: string;
}

export interface StatusChange {
  from_status: BookingStatus | null;
  to_status: BookingStatus;
  reason: string;
  created_at: string;
}

export interface Payment {
  status: string;
  amount: Money;
  refunded: Money;
  provider: string;
  provider_order_id: string;
  approval_url: string | null;
}

export interface Payout {
  status: "scheduled" | "blocked" | "queued" | "sent" | "paid" | "failed" | "cancelled";
  amount: Money;
  host_receives: Money | null;
  fx_rate: string | null;
  release_at: string;
  provider_payout_id: string | null;
  failure_reason: string | null;
  review_reason: string | null;
}

export interface Refund {
  id: string;
  status: "pending" | "succeeded" | "failed";
  source: "cancellation" | "dispute";
  amount: Money;
  from_host: Money;
  from_fee: Money;
  reason: string;
  created_at: string;
}

export interface Risk {
  score: number;
  action: "allow" | "review" | "hold";
  signals: { code: string; label: string; weight: number; detail: string }[];
  explanation: string;
  explained_by: string;
  reviewed_at: string | null;
}

export interface Booking {
  id: string;
  status: BookingStatus;
  listing: Listing;
  guest_id: string;
  host_id: string;
  check_in: string;
  check_out: string;
  nights: number;
  total: Money;
  cancelled_at: string | null;
  payment: Payment | null;
  payout: Payout | null;
  refunds: Refund[];
  risk: Risk | null;
  dispute_ids: string[];
  history: StatusChange[];
  created_at: string;
}

export interface BookingSummary {
  id: string;
  status: BookingStatus;
  listing_title: string;
  check_in: string;
  check_out: string;
  total: Money;
  created_at: string;
}

export interface Checkout {
  booking_id: string;
  provider_order_id: string;
  approval_url: string | null;
}

export interface CancellationQuote {
  policy: string;
  refund: Money;
  refund_percent: number;
  explanation: string;
}

export interface Evidence {
  id: string;
  party: "guest" | "host";
  kind: "statement" | "photo";
  content: string;
  url: string | null;
  created_at: string;
}

export interface Dispute {
  id: string;
  booking_id: string;
  status: "open" | "recommended" | "resolved";
  reason: string;
  claim: Money;
  max_refundable: Money | null;
  evidence: Evidence[];
  recommendation: {
    refund: Money;
    reasoning: string;
    cited_evidence_ids: string[];
    confidence: number;
    recommended_by: string;
  } | null;
  decision_refund: Money | null;
  decision_note: string | null;
  decided_at: string | null;
  created_at: string;
}

export interface HostSummary {
  owed: Money[];
  payouts: { booking_id: string; listing_title: string; check_in: string; payout: Payout }[];
}

export interface AdminSummary {
  bookings_by_status: { label: BookingStatus; count: number }[];
  funds_held: Money[];
  owed_to_hosts: Money[];
  platform_revenue: Money[];
  open_disputes: number;
  blocked_payouts: number;
  risk_reviews: number;
  dead_events: number;
}

export interface OutboxEvent {
  id: string;
  topic: string;
  aggregate_id: string;
  status: string;
  attempts: number;
  next_attempt_at: string;
  last_error: string | null;
}

export interface ReconciliationRun {
  id: string;
  started_at: string;
  finished_at: string;
  checks_run: number;
  findings: { code: string; severity: "critical" | "warning" | "info"; message: string; booking_id: string | null }[];
  summary: string;
  summarized_by: string;
}

export interface Problem {
  type: string;
  title: string;
  status: number;
  detail: string;
}
