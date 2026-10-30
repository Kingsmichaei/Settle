import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { BookingSummary } from "../api/types";
import { Empty, ErrorNote, Loading, Page, StatusPill } from "../components/ui";
import { formatStay } from "../lib/format";
import { useAsync } from "../lib/useAsync";

export function BookingTable({ bookings, linkTo }: { bookings: BookingSummary[]; linkTo: (id: string) => string }) {
  return (
    <div className="overflow-x-auto rounded-xl border border-line bg-white">
      <table className="w-full min-w-[560px] text-left text-sm">
        <thead className="border-b border-line text-ink-muted">
          <tr>
            <th className="px-4 py-3 font-medium">Stay</th>
            <th className="px-4 py-3 font-medium">Dates</th>
            <th className="px-4 py-3 font-medium">Status</th>
            <th className="px-4 py-3 text-right font-medium">Total</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-line">
          {bookings.map((booking) => (
            <tr key={booking.id} className="hover:bg-tile/40">
              <td className="px-4 py-3">
                <Link to={linkTo(booking.id)} className="font-semibold text-cobalt hover:underline">
                  {booking.listing_title}
                </Link>
              </td>
              <td className="px-4 py-3 text-ink-muted">{formatStay(booking.check_in, booking.check_out)}</td>
              <td className="px-4 py-3"><StatusPill status={booking.status} /></td>
              <td className="px-4 py-3 text-right font-medium">{booking.total.formatted}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function TripsPage() {
  const { data, error, loading } = useAsync(api.bookings, []);
  return (
    <Page title="My trips">
      <ErrorNote>{error}</ErrorNote>
      {loading && <Loading />}
      {data?.length === 0 && (
        <Empty title="No trips yet">
          <Link to="/stays" className="font-medium text-cobalt hover:underline">Find a stay</Link> to book your first one.
        </Empty>
      )}
      {data && data.length > 0 && <BookingTable bookings={data} linkTo={(id) => `/trips/${id}`} />}
    </Page>
  );
}
