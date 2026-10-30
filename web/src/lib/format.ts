import type { BookingStatus } from "../api/types";

const dateFormat = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short", year: "numeric" });
const shortDate = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const dateTime = new Intl.DateTimeFormat("en-GB", {
  day: "numeric",
  month: "short",
  hour: "2-digit",
  minute: "2-digit",
  timeZone: "UTC",
  timeZoneName: "short",
});

const parseDay = (iso: string) => new Date(`${iso.slice(0, 10)}T00:00:00Z`);

export const formatDate = (iso: string) => dateFormat.format(parseDay(iso));
export const formatShort = (iso: string) => shortDate.format(parseDay(iso));
export const formatDateTime = (iso: string) => dateTime.format(new Date(iso));
export const formatStay = (checkIn: string, checkOut: string) =>
  `${formatShort(checkIn)} to ${formatDate(checkOut)}`;

/** What each status means, in words a guest or host would use. */
export const STATUS_LABEL: Record<BookingStatus, string> = {
  pending: "Awaiting payment",
  authorized: "Payment approved",
  captured: "Paid",
  held: "Paid, held until check-in",
  disputed: "Under review",
  partially_refunded: "Partly refunded",
  released: "Paying the host",
  paid_out: "Host paid",
  payout_failed: "Payout failed",
  voided: "Cancelled",
  refunded: "Refunded",
};

export type Tone = "neutral" | "held" | "settled" | "failed" | "progress";

export const STATUS_TONE: Record<BookingStatus, Tone> = {
  pending: "neutral",
  authorized: "progress",
  captured: "progress",
  held: "held",
  disputed: "held",
  partially_refunded: "held",
  released: "progress",
  paid_out: "settled",
  payout_failed: "failed",
  voided: "neutral",
  refunded: "neutral",
};

/** Same shape the API returns ("USD 1,234.56"), for amounts computed in the browser. */
export function formatMoney(minor: number, currency: string): string {
  const digits = new Intl.NumberFormat("en-US", { style: "currency", currency }).resolvedOptions()
    .maximumFractionDigits ?? 2;
  const value = (minor / 10 ** digits).toLocaleString("en-US", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
  return `${currency} ${value}`;
}

export const toMinor = (major: string) => Math.round(Number.parseFloat(major || "0") * 100);
export const toMajor = (minor: number) => (minor / 100).toFixed(2);
