import { Link } from "react-router-dom";

import { api } from "../api/client";
import { TileArt } from "../components/TileArt";
import { Empty, ErrorNote, Loading, Page } from "../components/ui";
import { useAsync } from "../lib/useAsync";

export function StaysPage() {
  const { data: listings, error, loading } = useAsync(api.listings, []);
  return (
    <Page title="Stays" intro="Prices are per night. Your card is only charged once the host's calendar is confirmed.">
      <ErrorNote>{error}</ErrorNote>
      {loading && <Loading />}
      {listings?.length === 0 && <Empty title="No stays listed yet" />}
      <ul className="grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
        {listings?.map((listing) => (
          <li key={listing.id}>
            <Link to={`/stays/${listing.id}`} className="group block">
              <TileArt seed={listing.id} className="aspect-[3/2] w-full rounded-lg" />
              <div className="mt-3 flex items-baseline justify-between gap-3">
                <h2 className="font-semibold group-hover:text-cobalt">{listing.title}</h2>
                <span className="whitespace-nowrap font-semibold">{listing.nightly_price.formatted}</span>
              </div>
              <p className="text-sm text-ink-muted">
                {listing.city}, hosted by {listing.host_name}
              </p>
              <p className="mt-1 text-sm text-ink-muted">{listing.policy_summary}</p>
            </Link>
          </li>
        ))}
      </ul>
    </Page>
  );
}
