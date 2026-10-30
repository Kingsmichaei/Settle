import { useMemo, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router-dom";

import { api } from "../api/client";
import { TileArt } from "../components/TileArt";
import { Button, ErrorNote, Field, Loading, Page, Row } from "../components/ui";
import { formatMoney } from "../lib/format";
import { useAction, useAsync } from "../lib/useAsync";

const isoDay = (offsetDays: number) => {
  const day = new Date();
  day.setUTCDate(day.getUTCDate() + offsetDays);
  return day.toISOString().slice(0, 10);
};

export function StayDetailPage() {
  const { listingId = "" } = useParams();
  const navigate = useNavigate();
  const { data: listing, error } = useAsync(() => api.listing(listingId), [listingId]);
  const [checkIn, setCheckIn] = useState(isoDay(30));
  const [checkOut, setCheckOut] = useState(isoDay(34));

  const nights = useMemo(() => {
    const ms = Date.parse(checkOut) - Date.parse(checkIn);
    return Number.isFinite(ms) ? Math.round(ms / 86_400_000) : 0;
  }, [checkIn, checkOut]);

  const reserve = useAction(async () => {
    const booking = await api.book(listingId, checkIn, checkOut);
    const checkout = await api.checkout(booking.id);
    if (checkout.approval_url) window.location.assign(checkout.approval_url);
    else navigate(`/trips/${booking.id}`);
  });

  if (error) return <Page title="Stay"><ErrorNote>{error}</ErrorNote></Page>;
  if (!listing) return <Page title="Stay"><Loading /></Page>;

  const price = listing.nightly_price;
  const totalText = formatMoney(nights > 0 ? price.amount_minor * nights : 0, price.currency);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void reserve.run();
  };

  return (
    <Page title={listing.title} intro={`${listing.city}, hosted by ${listing.host_name}`}>
      <div className="grid gap-10 lg:grid-cols-[1.3fr_1fr]">
        <div>
          <TileArt seed={listing.id} className="aspect-[3/2] w-full rounded-xl" />
          <h2 className="mt-6 text-lg font-semibold">Cancellation</h2>
          <p className="mt-1 text-ink-muted">{listing.policy_summary}</p>
          <h2 className="mt-6 text-lg font-semibold">How paying works</h2>
          <p className="mt-1 max-w-prose text-ink-muted">
            You approve the payment with PayPal. We hold it and only pay {listing.host_name} 24 hours after
            check-in, so if something is wrong on arrival you can open a dispute while the money is still here.
          </p>
        </div>

        <form onSubmit={submit} className="self-start rounded-xl border border-line bg-white p-6">
          <p className="text-xl font-bold">
            {price.formatted} <span className="text-base font-normal text-ink-muted">per night</span>
          </p>
          <div className="mt-5 grid grid-cols-2 gap-3">
            <Field label="Check-in" type="date" required min={isoDay(0)} value={checkIn}
              onChange={(e) => setCheckIn(e.target.value)} />
            <Field label="Check-out" type="date" required min={checkIn} value={checkOut}
              onChange={(e) => setCheckOut(e.target.value)} />
          </div>
          <dl className="mt-5 border-t border-line pt-3">
            <Row label={`${price.formatted} × ${Math.max(nights, 0)} nights`}>{totalText}</Row>
            <Row label="Total">{totalText}</Row>
          </dl>
          <div className="mt-4 space-y-3">
            <ErrorNote>{reserve.error}</ErrorNote>
            <Button type="submit" busy={reserve.busy} disabled={nights <= 0} className="w-full">
              Reserve and pay with PayPal
            </Button>
          </div>
        </form>
      </div>
    </Page>
  );
}
