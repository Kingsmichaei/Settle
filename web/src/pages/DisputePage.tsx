import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router-dom";

import { api } from "../api/client";
import type { Dispute } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { Button, ErrorNote, Field, Loading, Page, Pill, Section, TextArea } from "../components/ui";
import { formatDateTime, toMajor, toMinor } from "../lib/format";
import { useAction, useAsync } from "../lib/useAsync";

const STATUS_TEXT: Record<Dispute["status"], string> = {
  open: "Collecting evidence",
  recommended: "Recommendation ready",
  resolved: "Decided",
};

export function DisputePage() {
  const { disputeId = "" } = useParams();
  const { user } = useAuth();
  // After asking the mediator, refresh until its recommendation arrives from the worker.
  const [waitingForRecommendation, setWaiting] = useState(false);
  const state = useAsync(() => api.dispute(disputeId), [disputeId], {
    pollMs: 2000,
    pollWhile: (d) => d.status === "open" && waitingForRecommendation,
  });
  const dispute = state.data;

  if (state.error) return <Page title="Dispute"><ErrorNote>{state.error}</ErrorNote></Page>;
  if (!dispute) return <Page title="Dispute"><Loading /></Page>;

  const isAdmin = user?.role === "admin";
  const resolved = dispute.status === "resolved";

  return (
    <Page
      title={dispute.reason}
      intro={<>Claim of {dispute.claim.formatted} on <Link className="text-cobalt hover:underline" to={`/trips/${dispute.booking_id}`}>this booking</Link>. The host's payout waits until it's decided.</>}
      actions={<Pill tone={resolved ? "settled" : "held"}>{STATUS_TEXT[dispute.status]}</Pill>}
    >
      <div className="grid gap-10 lg:grid-cols-[1.4fr_1fr]">
        <div className="space-y-10">
          <Section title="Evidence">
            {dispute.evidence.length === 0 && <p className="text-sm text-ink-muted">No evidence yet.</p>}
            <ul className="space-y-3">
              {dispute.evidence.map((item) => {
                const cited = dispute.recommendation?.cited_evidence_ids.includes(item.id);
                return (
                  <li key={item.id} className={`rounded-lg border bg-white p-4 ${cited ? "border-cobalt" : "border-line"}`}>
                    <p className="text-sm">
                      <span className="font-semibold capitalize">{item.party}</span>
                      <span className="text-ink-muted"> {item.kind === "photo" ? "added a photo" : "said"} on {formatDateTime(item.created_at)}</span>
                      {cited && <span className="text-cobalt"> (cited by the mediator)</span>}
                    </p>
                    <p className="mt-1">{item.content}</p>
                    {item.url && (
                      <a href={item.url} target="_blank" rel="noreferrer" className="mt-1 inline-block text-sm text-cobalt hover:underline">
                        Open photo
                      </a>
                    )}
                  </li>
                );
              })}
            </ul>
          </Section>
          {!resolved && !isAdmin && <AddEvidence disputeId={dispute.id} onAdded={state.reload} />}
        </div>

        <div className="space-y-10">
          <Recommendation dispute={dispute} />
          {isAdmin && !resolved && (
            <AdminDecision
              key={dispute.recommendation?.refund.amount_minor ?? "none"}
              dispute={dispute}
              onChange={state.reload}
              onRequested={() => setWaiting(true)}
            />
          )}
          {resolved && dispute.decision_refund && (
            <Section title="Decision">
              <p className="text-sm">
                <span className="font-semibold">{dispute.decision_refund.formatted} refunded to the guest.</span>{" "}
                {dispute.decision_note}
              </p>
            </Section>
          )}
        </div>
      </div>
    </Page>
  );
}

function Recommendation({ dispute }: { dispute: Dispute }) {
  const rec = dispute.recommendation;
  if (!rec) return null;
  return (
    <Section title="Mediator's recommendation" aside={<span className="text-xs text-ink-faint">{rec.recommended_by}</span>}>
      <p className="text-2xl font-bold tracking-tight">{rec.refund.formatted}</p>
      <p className="text-sm text-ink-muted">refund to the guest, confidence {Math.round(rec.confidence * 100)}%</p>
      <p className="mt-3 text-sm">{rec.reasoning}</p>
      <p className="mt-3 text-xs text-ink-faint">The mediator only recommends. An operator makes the decision.</p>
    </Section>
  );
}

function AddEvidence({ disputeId, onAdded }: { disputeId: string; onAdded: () => Promise<void> }) {
  const [kind, setKind] = useState<"statement" | "photo">("statement");
  const [content, setContent] = useState("");
  const [url, setUrl] = useState("");
  const add = useAction(async () => {
    await api.addEvidence(disputeId, kind, content, kind === "photo" ? url : undefined);
    setContent("");
    setUrl("");
    await onAdded();
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void add.run();
  };
  return (
    <Section title="Add evidence">
      <form onSubmit={submit} className="space-y-3">
        <fieldset className="flex gap-4 text-sm">
          <legend className="sr-only">Kind of evidence</legend>
          {(["statement", "photo"] as const).map((option) => (
            <label key={option} className="flex items-center gap-2">
              <input type="radio" name="kind" checked={kind === option} onChange={() => setKind(option)} />
              {option === "statement" ? "A statement" : "A photo link"}
            </label>
          ))}
        </fieldset>
        {kind === "photo" && (
          <Field label="Photo link" type="url" required value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://" />
        )}
        <TextArea label={kind === "photo" ? "What the photo shows" : "Your statement"} required value={content}
          onChange={(e) => setContent(e.target.value)} />
        <ErrorNote>{add.error}</ErrorNote>
        <Button type="submit" busy={add.busy}>Add evidence</Button>
      </form>
    </Section>
  );
}

function AdminDecision({ dispute, onChange, onRequested }: {
  dispute: Dispute;
  onChange: () => Promise<void>;
  onRequested: () => void;
}) {
  const suggested = dispute.recommendation?.refund.amount_minor;
  const [amount, setAmount] = useState(suggested !== undefined ? toMajor(suggested) : "");
  const [note, setNote] = useState("");
  const recommend = useAction(async () => {
    await api.admin.recommend(dispute.id);
    onRequested();
    await onChange();
  });
  const decide = useAction(async () => {
    await api.admin.decide(dispute.id, toMinor(amount), dispute.claim.currency, note);
    await onChange();
  });
  const submit = (event: FormEvent) => {
    event.preventDefault();
    void decide.run();
  };
  return (
    <Section title="Decide">
      <Button variant="secondary" busy={recommend.busy} onClick={() => void recommend.run()}>
        {dispute.recommendation ? "Ask the mediator again" : "Ask the mediator"}
      </Button>
      <ErrorNote>{recommend.error}</ErrorNote>
      <form onSubmit={submit} className="mt-5 space-y-3">
        <Field label={`Refund to the guest (${dispute.claim.currency})`} type="number" min="0" step="0.01" required
          value={amount} onChange={(e) => setAmount(e.target.value)}
          hint={dispute.max_refundable ? `Up to ${dispute.max_refundable.formatted}. Zero releases the full payout to the host.` : undefined} />
        <TextArea label="Note for the guest and host" value={note} onChange={(e) => setNote(e.target.value)} />
        <ErrorNote>{decide.error}</ErrorNote>
        <Button type="submit" busy={decide.busy}>Decide dispute</Button>
      </form>
    </Section>
  );
}
