import { useState, type FormEvent } from "react";
import { Link } from "react-router-dom";

import { api } from "../api/client";
import type { Payout } from "../api/types";
import { Button, Empty, ErrorNote, Field, Loading, Page, Pill, Section } from "../components/ui";
import { formatDate, formatDateTime } from "../lib/format";
import { useAction, useAsync } from "../lib/useAsync";

const PAYOUT_TEXT: Record<Payout["status"], string> = {
  scheduled: "Scheduled",
  blocked: "On hold for review",
  queued: "Sending",
  sent: "Sent, awaiting confirmation",
  paid: "Paid",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function EarningsPage() {
  const { data, error, loading } = useAsync(api.hostSummary, []);
  const owed = data?.owed.filter((money) => money.amount_minor !== 0) ?? [];
  return (
    <Page
      title="Earnings"
      intro={
        data
          ? owed.length
            ? `We're holding ${owed.map((m) => m.formatted).join(" and ")} for you. Each payout is released 24 hours after your guest checks in.`
            : "Nothing is owed to you right now."
          : undefined
      }
    >
      <ErrorNote>{error}</ErrorNote>
      {loading && <Loading />}
      <div className="grid gap-10 lg:grid-cols-[1.5fr_1fr]">
        <Section title="Payouts">
          {data?.payouts.length === 0 && <Empty title="No payouts yet">They appear here once a guest has paid.</Empty>}
          {data && data.payouts.length > 0 && (
            <ul className="divide-y divide-line rounded-xl border border-line bg-white">
              {data.payouts.map(({ booking_id, listing_title, check_in, payout }) => (
                <li key={booking_id} className="flex flex-wrap items-center gap-x-6 gap-y-1 px-4 py-3">
                  <div className="min-w-0 flex-1">
                    <Link to={`/trips/${booking_id}`} className="font-semibold text-cobalt hover:underline">{listing_title}</Link>
                    <p className="text-sm text-ink-muted">
                      Check-in {formatDate(check_in)}, released {formatDateTime(payout.release_at)}
                    </p>
                  </div>
                  <div className="text-right">
                    <p className="font-semibold">{payout.host_receives?.formatted ?? payout.amount.formatted}</p>
                    {payout.host_receives && payout.host_receives.currency !== payout.amount.currency && (
                      <p className="text-xs text-ink-muted">{payout.amount.formatted} at {payout.fx_rate}</p>
                    )}
                  </div>
                  <Pill tone={payout.status === "paid" ? "settled" : payout.status === "failed" ? "failed" : payout.status === "blocked" ? "held" : "progress"}>
                    {PAYOUT_TEXT[payout.status]}
                  </Pill>
                </li>
              ))}
            </ul>
          )}
        </Section>
        <Assistant />
      </div>
    </Page>
  );
}

interface Exchange {
  question: string;
  answer: string;
  by: string;
}

const SUGGESTIONS = ["When is my next payout?", "How much do you owe me?", "Did any payout fail?"];

function Assistant() {
  const [question, setQuestion] = useState("");
  const [exchanges, setExchanges] = useState<Exchange[]>([]);
  const ask = useAction(async (asked: string) => {
    const reply = await api.askAssistant(asked);
    setExchanges((previous) => [...previous, { question: asked, answer: reply.answer, by: reply.answered_by }]);
    setQuestion("");
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (question.trim()) void ask.run(question.trim());
  };
  return (
    <Section title="Ask about your earnings">
      <div className="rounded-xl border border-line bg-white p-4">
        {exchanges.length === 0 && (
          <div className="flex flex-wrap gap-2">
            {SUGGESTIONS.map((suggestion) => (
              <button key={suggestion} type="button" onClick={() => void ask.run(suggestion)}
                className="rounded-full border border-line px-3 py-1 text-sm hover:border-cobalt hover:text-cobalt">
                {suggestion}
              </button>
            ))}
          </div>
        )}
        <ol className="space-y-4" aria-live="polite">
          {exchanges.map((exchange, index) => (
            <li key={index}>
              <p className="text-sm font-semibold">{exchange.question}</p>
              <p className="mt-1 text-sm">{exchange.answer}</p>
              <p className="mt-1 text-xs text-ink-faint">Answered from your payout records ({exchange.by})</p>
            </li>
          ))}
        </ol>
        <form onSubmit={submit} className="mt-4 flex items-end gap-2">
          <div className="flex-1">
            <Field label="Your question" value={question} maxLength={500} onChange={(e) => setQuestion(e.target.value)} />
          </div>
          <Button type="submit" busy={ask.busy}>Ask</Button>
        </form>
        <ErrorNote>{ask.error}</ErrorNote>
      </div>
    </Section>
  );
}
