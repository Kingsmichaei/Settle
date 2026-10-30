import { useState, type FormEvent } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Booking, BookingStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { History } from "../components/History";
import { MoneyStrip } from "../components/MoneyStrip";
import { Button, ErrorNote, Field, Loading, Page, Pill, Row, Section, StatusPill, TextArea } from "../components/ui";
import { formatDate, formatDateTime, formatStay, toMajor, toMinor } from "../lib/format";
import { useAction, useAsync } from "../lib/useAsync";

// While the worker is moving money the page refreshes itself.
const MOVING: BookingStatus[] = ["authorized", "captured", "released"];
const isMoving = (booking: Booking) =>
  MOVING.includes(booking.status) ||
  booking.refunds.some((refund) => refund.status === "pending") ||
  (booking.status === "pending" && booking.history.length > 0 && booking.risk !== null);

export function TripDetailPage() {
  const { bookingId = "" } = useParams();
  const { user } = useAuth();
  const state = useAsync(() => api.booking(bookingId), [bookingId], { pollMs: 2000, pollWhile: isMoving });
  const booking = state.data;

  if (state.error) return <Page title="Booking"><ErrorNote>{state.error}</ErrorNote></Page>;
  if (!booking) return <Page title="Booking"><Loading /></Page>;

  const isGuest = user?.id === booking.guest_id;
  const isAdmin = user?.role === "admin";

  return (
    <Page
      title={booking.listing.title}
      intro={`${formatStay(booking.check_in, booking.check_out)}, ${booking.nights} nights in ${booking.listing.city}`}
      actions={<StatusPill status={booking.status} />}
    >
      <MoneyStrip booking={booking} />

      {isGuest && <GuestActions booking={booking} onChange={state.reload} />}
      {isAdmin && <AdminActions booking={booking} onChange={state.reload} />}

      <div className="grid gap-10 lg:grid-cols-2">
        <Section title="Payment">
          <dl>
            <Row label="Charged">{booking.payment?.amount.formatted ?? "Not yet"}</Row>
            {booking.payment && booking.payment.refunded.amount_minor > 0 && (
              <Row label="Refunded">{booking.payment.refunded.formatted}</Row>
            )}
            <Row label="Provider">{booking.payment?.provider ?? "PayPal"}</Row>
            <Row label="Cancellation policy">{booking.listing.policy_summary}</Row>
          </dl>
          {booking.refunds.length > 0 && (
            <ul className="mt-4 space-y-2">
              {booking.refunds.map((refund) => (
                <li key={refund.id} className="rounded-md bg-tile/60 px-3 py-2 text-sm">
                  <span className="font-semibold">{refund.amount.formatted}</span> back to the guest
                  <span className="text-ink-muted"> ({refund.status}): {refund.reason}</span>
                </li>
              ))}
            </ul>
          )}
        </Section>

        <Section title="Host payout">
          {booking.payout ? (
            <dl>
              <Row label="Amount">{booking.payout.amount.formatted}</Row>
              {booking.payout.host_receives && booking.payout.host_receives.currency !== booking.payout.amount.currency && (
                <Row label="Host receives">
                  {booking.payout.host_receives.formatted}
                  <span className="font-normal text-ink-muted"> at {booking.payout.fx_rate}</span>
                </Row>
              )}
              <Row label="Released">{formatDateTime(booking.payout.release_at)}</Row>
              <Row label="Status"><Pill tone={payoutTone(booking.payout.status)}>{booking.payout.status}</Pill></Row>
              {(booking.payout.review_reason || booking.payout.failure_reason) && (
                <p className="mt-2 text-sm text-held">{booking.payout.review_reason ?? booking.payout.failure_reason}</p>
              )}
            </dl>
          ) : (
            <p className="text-sm text-ink-muted">Scheduled once the payment is captured.</p>
          )}
        </Section>
      </div>

      {booking.dispute_ids.length > 0 && (
        <Section title="Disputes">
          <ul className="space-y-1 text-sm">
            {booking.dispute_ids.map((id, index) => (
              <li key={id}>
                <Link to={`/disputes/${id}`} className="font-medium text-cobalt hover:underline">
                  Dispute {index + 1}
                </Link>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {isAdmin && booking.risk && (
        <Section title="Risk assessment" aside={<span className="text-xs text-ink-faint">{booking.risk.explained_by}</span>}>
          <p className="text-sm">
            <span className="font-semibold">Score {booking.risk.score}, {booking.risk.action}.</span> {booking.risk.explanation}
          </p>
        </Section>
      )}

      <Section title="History">
        <History changes={booking.history} />
      </Section>
    </Page>
  );
}

function payoutTone(status: NonNullable<Booking["payout"]>["status"]) {
  if (status === "paid") return "settled" as const;
  if (status === "failed") return "failed" as const;
  if (status === "blocked") return "held" as const;
  return "progress" as const;
}

function GuestActions({ booking, onChange }: { booking: Booking; onChange: () => Promise<void> }) {
  const navigate = useNavigate();
  const quote = useAsync(() => api.cancellationQuote(booking.id), [booking.id, booking.status]);
  const [confirming, setConfirming] = useState(false);
  const cancel = useAction(async () => {
    await api.cancel(booking.id, "cancelled by guest");
    setConfirming(false);
    await onChange();
  });

  const awaitingApproval = booking.status === "pending" && booking.payment?.approval_url && !booking.risk;
  const cancellable = !booking.cancelled_at && ["pending", "authorized", "held"].includes(booking.status);
  const disputable = booking.status === "held" && !booking.cancelled_at;

  if (!awaitingApproval && !cancellable && !disputable) return null;

  return (
    <div className="flex flex-wrap items-start gap-4 rounded-xl border border-line bg-white p-5">
      {awaitingApproval && (
        <div className="flex-1">
          <p className="font-semibold">Your payment isn't approved yet.</p>
          <Button className="mt-2" onClick={() => window.location.assign(booking.payment!.approval_url!)}>
            Continue to PayPal
          </Button>
        </div>
      )}
      {cancellable && (
        <div className="flex-1">
          <p className="font-semibold">Cancel this booking</p>
          <p className="text-sm text-ink-muted">
            {quote.data ? `You'd get ${quote.data.refund.formatted} back. ${quote.data.explanation}.` : "Checking your refund…"}
          </p>
          {confirming ? (
            <div className="mt-2 flex gap-2">
              <Button variant="danger" busy={cancel.busy} onClick={() => void cancel.run()}>Yes, cancel booking</Button>
              <Button variant="secondary" onClick={() => setConfirming(false)}>Keep booking</Button>
            </div>
          ) : (
            <Button variant="secondary" className="mt-2" onClick={() => setConfirming(true)}>Cancel booking</Button>
          )}
          <ErrorNote>{cancel.error}</ErrorNote>
        </div>
      )}
      {disputable && <OpenDispute booking={booking} onOpened={(id) => navigate(`/disputes/${id}`)} />}
    </div>
  );
}

function OpenDispute({ booking, onOpened }: { booking: Booking; onOpened: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [reason, setReason] = useState("");
  const [amount, setAmount] = useState("");
  const [statement, setStatement] = useState("");
  const submit = useAction(async () => {
    const dispute = await api.openDispute(booking.id, reason, toMinor(amount), booking.total.currency, statement);
    onOpened(dispute.id);
  });

  if (!open) {
    return (
      <div className="flex-1">
        <p className="font-semibold">Something wrong with the stay?</p>
        <p className="text-sm text-ink-muted">The host isn't paid until {formatDate(booking.payout?.release_at ?? booking.check_in)}.</p>
        <Button variant="secondary" className="mt-2" onClick={() => setOpen(true)}>Report a problem</Button>
      </div>
    );
  }
  const onSubmit = (event: FormEvent) => {
    event.preventDefault();
    void submit.run();
  };
  return (
    <form onSubmit={onSubmit} className="w-full space-y-3 border-t border-line pt-4">
      <p className="font-semibold">Report a problem</p>
      <Field label="What went wrong?" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)}
        placeholder="No hot water for the first two nights" />
      <Field label={`Refund you're asking for (${booking.total.currency})`} type="number" min="0.01" step="0.01"
        max={toMajor(booking.total.amount_minor)} required value={amount} onChange={(e) => setAmount(e.target.value)} />
      <TextArea label="Tell us what happened" value={statement} onChange={(e) => setStatement(e.target.value)} />
      <ErrorNote>{submit.error}</ErrorNote>
      <div className="flex gap-2">
        <Button type="submit" busy={submit.busy}>Open dispute</Button>
        <Button variant="secondary" onClick={() => setOpen(false)}>Not now</Button>
      </div>
    </form>
  );
}

function AdminActions({ booking, onChange }: { booking: Booking; onChange: () => Promise<void> }) {
  const approve = useAction(async () => {
    await api.admin.approvePayout(booking.id);
    await onChange();
  });
  const retry = useAction(async () => {
    await api.admin.retryPayout(booking.id);
    await onChange();
  });
  const blocked = booking.payout?.status === "blocked";
  const failed = booking.status === "payout_failed";
  if (!blocked && !failed) return null;
  return (
    <div className="rounded-xl border border-held/40 bg-held-soft p-5">
      {blocked && (
        <>
          <p className="font-semibold">Payout held for review</p>
          <p className="text-sm">{booking.payout?.review_reason}</p>
          <Button className="mt-3" busy={approve.busy} onClick={() => void approve.run()}>Approve payout</Button>
          <ErrorNote>{approve.error}</ErrorNote>
        </>
      )}
      {failed && (
        <>
          <p className="font-semibold">Payout failed</p>
          <p className="text-sm">{booking.payout?.failure_reason}</p>
          <Button className="mt-3" busy={retry.busy} onClick={() => void retry.run()}>Retry payout</Button>
          <ErrorNote>{retry.error}</ErrorNote>
        </>
      )}
    </div>
  );
}
