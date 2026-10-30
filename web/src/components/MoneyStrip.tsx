import type { Booking, BookingStatus } from "../api/types";
import { formatMoney, STATUS_LABEL } from "../lib/format";

/**
 * Where every cent of this booking is right now: one bar, split into the host's share,
 * the platform fee and anything refunded, over a row of tiles for the payment lifecycle.
 */
const STAGES: BookingStatus[] = ["pending", "authorized", "held", "released", "paid_out"];
const STAGE_NAMES: Record<string, string> = {
  pending: "Booked",
  authorized: "Approved",
  held: "Held",
  released: "Released",
  paid_out: "Host paid",
};

function stageIndex(booking: Booking): number {
  const reached = new Set(booking.history.map((change) => change.to_status));
  let index = 0;
  STAGES.forEach((stage, i) => {
    if (reached.has(stage)) index = i;
  });
  return index;
}

const ENDED_EARLY: Partial<Record<BookingStatus, string>> = {
  voided: "Cancelled before any charge",
  refunded: "Refunded to the guest in full",
  payout_failed: "The payout was refused; it can be retried",
  disputed: "A dispute is open; the payout waits for a decision",
};

export function MoneyStrip({ booking }: { booking: Booking }) {
  const total = booking.total.amount_minor;
  const refunded = booking.payment?.refunded.amount_minor ?? 0;
  const hostShare = booking.payout?.amount.amount_minor ?? 0;
  const fee = Math.max(0, total - refunded - hostShare);
  const charged = booking.payment && ["captured", "partially_refunded", "refunded"].includes(booking.payment.status);
  const current = stageIndex(booking);
  const ended = ENDED_EARLY[booking.status];
  const pct = (part: number) => `${(part / total) * 100}%`;
  const currency = booking.total.currency;
  const money = (minor: number) => formatMoney(minor, currency);

  const segments = charged
    ? [
        { key: "host", minor: hostShare, label: "Host's share", className: "bg-cobalt" },
        { key: "fee", minor: fee, label: "Platform fee", className: "bg-cobalt-soft/60" },
        { key: "refund", minor: refunded, label: "Refunded to guest", className: "bg-held" },
      ].filter((segment) => segment.minor > 0)
    : [];

  return (
    <figure className="rounded-xl border border-line bg-white p-5 sm:p-6">
      <figcaption className="flex flex-wrap items-baseline justify-between gap-3">
        <span className="text-lg font-semibold">Where the money is</span>
        <span className="text-2xl font-bold tracking-tight">{booking.total.formatted}</span>
      </figcaption>

      <div className="mt-4 flex h-4 overflow-hidden rounded-full bg-tile" aria-hidden="true">
        {segments.map((segment) => (
          <div key={segment.key} className={segment.className} style={{ width: pct(segment.minor) }} />
        ))}
      </div>
      {charged ? (
        <dl className="mt-3 grid gap-x-6 gap-y-1 text-sm sm:grid-cols-3">
          {segments.map((segment) => (
            <div key={segment.key} className="flex items-center gap-2">
              <span className={`h-2.5 w-2.5 rounded-sm ${segment.className}`} aria-hidden="true" />
              <dt className="text-ink-muted">{segment.label}</dt>
              <dd className="ml-auto font-semibold sm:ml-0">{money(segment.minor)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <p className="mt-3 text-sm text-ink-muted">Nothing has been charged yet.</p>
      )}

      <ol className="mt-6 grid grid-cols-5 gap-1.5" aria-label="Payment progress">
        {STAGES.map((stage, index) => {
          const done = !ended && index <= current;
          const isCurrent = !ended && index === current;
          return (
            <li key={stage} aria-current={isCurrent ? "step" : undefined}>
              <div
                className={[
                  "aspect-[4/1] rounded-sm border",
                  done ? "border-cobalt bg-cobalt" : "border-line bg-tile",
                  isCurrent ? "ring-2 ring-cobalt/30 ring-offset-1" : "",
                ].join(" ")}
              />
              <span className={`mt-1.5 block truncate text-[0.7rem] sm:text-xs ${done ? "font-semibold text-ink" : "text-ink-faint"}`}>
                {STAGE_NAMES[stage]}
              </span>
            </li>
          );
        })}
      </ol>
      <p className="mt-4 text-sm text-ink-muted">{ended ?? STATUS_LABEL[booking.status]}.</p>
    </figure>
  );
}
