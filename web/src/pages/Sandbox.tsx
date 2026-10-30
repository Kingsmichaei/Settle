import { useEffect } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { api } from "../api/client";
import { Button, ErrorNote, Loading } from "../components/ui";
import { useAction, useAsync } from "../lib/useAsync";

/**
 * Stand-in for PayPal's approval page when running with the fake provider. With PayPal the
 * guest approves on paypal.com and is sent back to /trips/:id/return instead.
 */
export function SandboxApprovePage() {
  const { orderId = "" } = useParams();
  const [params] = useSearchParams();
  const bookingId = params.get("ref") ?? "";
  const navigate = useNavigate();
  const { data: booking, error } = useAsync(() => api.booking(bookingId), [bookingId]);
  const approve = useAction(async () => {
    await api.confirm(bookingId);
    navigate(`/trips/${bookingId}`);
  });

  return (
    <main className="mx-auto max-w-md px-4 py-16">
      <div className="rounded-xl border border-line bg-white p-6">
        <p className="text-sm text-ink-muted">Sandbox payment provider</p>
        <h1 className="mt-1 text-xl font-bold">Approve this payment</h1>
        <ErrorNote>{error}</ErrorNote>
        {!booking && !error && <Loading />}
        {booking && (
          <>
            <p className="mt-4 text-3xl font-bold tracking-tight">{booking.total.formatted}</p>
            <p className="text-ink-muted">to Settle for {booking.listing.title}</p>
            <p className="mt-4 text-xs text-ink-faint">Order {orderId}. No real money moves in the sandbox.</p>
            <div className="mt-6 flex gap-2">
              <Button busy={approve.busy} onClick={() => void approve.run()}>Approve payment</Button>
              <Button variant="secondary" onClick={() => navigate(`/trips/${bookingId}`)}>Cancel</Button>
            </div>
            <ErrorNote>{approve.error}</ErrorNote>
          </>
        )}
      </div>
    </main>
  );
}

/** Where PayPal sends the guest back after approving. */
export function PaymentReturnPage() {
  const { bookingId = "" } = useParams();
  const navigate = useNavigate();
  const confirm = useAction(async () => {
    await api.confirm(bookingId);
    navigate(`/trips/${bookingId}`, { replace: true });
  });
  useEffect(() => {
    void confirm.run();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [bookingId]);
  return (
    <main className="mx-auto max-w-md px-4 py-16 text-center">
      {confirm.error ? <ErrorNote>{confirm.error}</ErrorNote> : <p className="text-ink-muted">Confirming your payment…</p>}
    </main>
  );
}
