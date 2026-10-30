import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Money, ReconciliationRun } from "../api/types";
import { Button, Empty, ErrorNote, Loading, Page, Pill, Section } from "../components/ui";
import { formatDateTime, STATUS_LABEL } from "../lib/format";
import { useAction, useAsync } from "../lib/useAsync";
import { BookingTable } from "./Trips";

const list = (amounts: Money[]) =>
  amounts.filter((m) => m.amount_minor !== 0).map((m) => m.formatted).join(" and ") || "nothing";

export function OpsPage() {
  const summary = useAsync(api.admin.summary, []);
  const risk = useAsync(api.admin.riskQueue, []);
  const disputes = useAsync(api.admin.disputes, []);
  const dead = useAsync(api.admin.deadEvents, []);
  const runs = useAsync(api.admin.reconciliationRuns, []);
  const bookings = useAsync(api.admin.bookings, []);
  const reconcile = useAction(async () => {
    await api.admin.reconcileNow();
    await Promise.all([runs.reload(), summary.reload()]);
  });

  const s = summary.data;
  const openDisputes = disputes.data?.filter((d) => d.status !== "resolved") ?? [];

  return (
    <Page
      title="Operations"
      intro={
        s && (
          <>
            We're holding {list(s.funds_held)} for guests' stays and owe hosts {list(s.owed_to_hosts)}.
            Fees earned: {list(s.platform_revenue)}.
          </>
        )
      }
    >
      <ErrorNote>{summary.error}</ErrorNote>
      {s && (
        <p className="-mt-6 text-sm text-ink-muted">
          {s.risk_reviews} to review for risk, {s.open_disputes} open disputes, {s.blocked_payouts} payouts on
          hold, {s.dead_events} failed background jobs.
        </p>
      )}

      <Section title="Reconciliation" aside={
        <Button variant="secondary" busy={reconcile.busy} onClick={() => void reconcile.run()}>Run checks now</Button>
      }>
        <ErrorNote>{reconcile.error}</ErrorNote>
        {runs.loading && <Loading />}
        {runs.data?.length === 0 && <Empty title="No runs yet">Checks run every few hours, or now with the button above.</Empty>}
        {runs.data?.[0] && <LatestRun run={runs.data[0]} />}
      </Section>

      <div className="grid gap-10 lg:grid-cols-2">
        <Section title="Risk reviews">
          {risk.data?.length === 0 && <p className="text-sm text-ink-muted">Nothing flagged.</p>}
          <ul className="space-y-2">
            {risk.data?.map((booking) => (
              <li key={booking.id} className="flex items-center justify-between gap-3 text-sm">
                <Link to={`/trips/${booking.id}`} className="font-medium text-cobalt hover:underline">{booking.listing_title}</Link>
                <span className="text-ink-muted">{booking.total.formatted}</span>
              </li>
            ))}
          </ul>
        </Section>

        <Section title="Open disputes">
          {disputes.data && openDisputes.length === 0 && <p className="text-sm text-ink-muted">No open disputes.</p>}
          <ul className="space-y-2">
            {openDisputes.map((dispute) => (
              <li key={dispute.id} className="flex items-center justify-between gap-3 text-sm">
                <Link to={`/disputes/${dispute.id}`} className="font-medium text-cobalt hover:underline">{dispute.reason}</Link>
                <span className="text-ink-muted">{dispute.claim.formatted}</span>
              </li>
            ))}
          </ul>
        </Section>
      </div>

      {dead.data && dead.data.length > 0 && (
        <Section title="Failed background jobs">
          <ul className="space-y-2 text-sm">
            {dead.data.map((event) => (
              <li key={event.id} className="rounded-md bg-failed-soft px-3 py-2">
                <span className="font-semibold">{event.topic}</span> gave up after {event.attempts} attempts:{" "}
                <span className="text-failed">{event.last_error}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section title="Recent bookings" aside={s && (
        <span className="text-xs text-ink-faint">
          {s.bookings_by_status.map((row) => `${row.count} ${STATUS_LABEL[row.label].toLowerCase()}`).join(", ")}
        </span>
      )}>
        {bookings.data && bookings.data.length > 0 ? (
          <BookingTable bookings={bookings.data} linkTo={(id) => `/trips/${id}`} />
        ) : (
          bookings.data && <Empty title="No bookings yet" />
        )}
      </Section>
    </Page>
  );
}

function LatestRun({ run }: { run: ReconciliationRun }) {
  const tone = run.findings.some((f) => f.severity === "critical")
    ? "failed"
    : run.findings.some((f) => f.severity === "warning")
      ? "held"
      : "settled";
  return (
    <div className="rounded-xl border border-line bg-white p-5">
      <div className="flex flex-wrap items-center gap-3">
        <Pill tone={tone}>{run.findings.length === 0 ? "Books balance" : `${run.findings.length} findings`}</Pill>
        <span className="text-sm text-ink-muted">
          {run.checks_run} checks, {formatDateTime(run.finished_at)}, summary by {run.summarized_by}
        </span>
      </div>
      <p className="mt-3">{run.summary}</p>
      {run.findings.length > 0 && (
        <ul className="mt-3 space-y-1 text-sm">
          {run.findings.map((finding, index) => (
            <li key={index}>
              <span className={finding.severity === "critical" ? "font-semibold text-failed" : finding.severity === "warning" ? "font-semibold text-held" : "font-semibold text-ink-muted"}>
                {finding.severity}
              </span>{" "}
              {finding.message}
              {finding.booking_id && (
                <> (<Link to={`/trips/${finding.booking_id}`} className="text-cobalt hover:underline">booking</Link>)</>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
