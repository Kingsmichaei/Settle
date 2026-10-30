# Settle

**A guest-to-host payments engine for rental marketplaces.** Settle charges guests through PayPal, holds the money until 24 hours after check-in, pays hosts in their own currency, and is built so it never loses or double-moves a cent, even when the provider times out, sends the same webhook twice, or a worker crashes halfway. AI agents score risk, mediate disputes, explain reconciliation reports and answer hosts' questions; deterministic code is the only thing that moves money.

```
$ settle demo
 1. Ada (US) books 4 nights at 'Sunny loft in Alfama', Lisbon: USD 500.00
 2. Approval arrives by webhook. Risk score 0 (allow).
 3. Captured and held: cash USD 500.00, owed to host USD 425.00, platform fee USD 75.00
 4. During the stay Ada disputes USD 100.00 for a broken boiler, with a photo
 5. Mediator (rules) recommends refunding USD 100.00: The guest's claim is supported by evidence and the host has not responded.
 6. Operator approves; partial refund sent. Host now owed USD 340.00, platform fee USD 60.00
 7. 24h after check-in: payout of USD 340.00 sent as EUR 312.80 (rate 0.92)
 8. Payout webhook delivered twice, processed once. Booking is 'paid_out'.
 9. Books: cash USD 60.00 = owed USD 0.00 + fees USD 60.00; guest refunded USD 100.00
10. Reconciliation: 1 finding(s) across 11 checks: 1 info. [info] Webhook EVT-PAYOUT-1 (payout_succeeded) was delivered 2 times and processed once
```

## What it does

| Area | What's built |
| --- | --- |
| Checkout | PayPal Orders v2 with `AUTHORIZE` intent; approval by return link or by webhook, whichever comes first |
| Holding funds | Captured immediately, held until 00:00 UTC on check-in + 24h, then released automatically |
| Payouts | PayPal Payouts in the host's currency (FX rate recorded on each payout); async confirmation by webhook |
| Cancellations | Flexible, moderate and strict policies; partial refunds funded proportionally from host share and fee |
| Disputes | Guests claim while funds are held; both sides add evidence; AI mediator recommends; an operator decides |
| Risk | Rule-based fraud signals score each booking at approval; high scores hold the payout for review |
| Webhooks | Signature-verified (PayPal's verification API), stored once, deliveries counted, processed in the background |
| Chargebacks | A reversed capture blocks the host's payout and raises a critical reconciliation finding |
| Reconciliation | Ledger vs records vs provider, every few hours or on demand, with an AI-written summary |
| Auth | Email and password (scrypt), signed JWT access tokens, role and ownership checks on every route |
| Frontend | React app for guests (book, pay, cancel, dispute), hosts (payouts, earnings assistant) and operators |

## Why it's built this way

Moving money is easy to get *almost* right. Each failure case has a specific defence:

| Failure | Defence | Where |
| --- | --- | --- |
| Client retries a request | `Idempotency-Key` on every write, scoped per user; retries replay the stored response | `idempotency/`, `api/idempotency.py` |
| Provider succeeds but the response is lost | Deterministic `PayPal-Request-Id` per operation; the retry returns the original result | `services/handlers.py` |
| Crash between saving state and calling the provider | Transactional outbox: state change and command commit together; a worker runs commands | `outbox/` |
| Duplicate or out-of-order events | Explicit state machine; webhook dedupe on provider event id | `domain/states.py`, `services/webhooks.py` |
| A webhook arrives before the state it confirms | The handler raises `RetryLater`; the outbox retries it with backoff | `services/handlers.py` |
| Two workers update one booking | Optimistic locking (`version` column) plus `FOR UPDATE SKIP LOCKED` on Postgres | `db/models/bookings.py`, `outbox/relay.py` |
| Paying a host money that's being refunded | Payout release skips bookings with a refund in flight; a refused refund blocks the payout | `services/payouts.py` |
| Money appearing or disappearing | Double-entry ledger; every entry balances; balances are derived, never stored | `ledger/` |
| Rounding errors | Integer minor units; fee rounded half-to-even, host gets the exact remainder | `domain/money.py`, `domain/fees.py` |
| A model making a bad call | AI output is schema-validated and clamped (a refund can't exceed the claim), never executed directly | `ai/` |
| Provider outage | Exponential backoff with full jitter; dead events surface on the ops page | `outbox/relay.py` |

## Architecture

```
  Guest / Host / Admin  ──►  React app (web/)
                                 │  JSON + bearer token + Idempotency-Key
                                 ▼
  PayPal webhooks  ──────►  FastAPI API ──► writes state + ledger + outbox in one transaction
                                 │
               ┌─────────────────┴──────────────────┐
               ▼                                    ▼
      SQLite (dev) / Postgres (prod)     Worker (APScheduler): outbox relay,
               ▲                         payout release, reconciliation
               └─────────────────────────────┬──────┘
                                              ▼
                         PaymentProvider (FakeProvider | PayPalProvider)
                         LLMClient (rules only | Gemini), versioned prompts
```

### Payment lifecycle

```
pending ─► authorized ─► captured ─► held ─────────────► released ─► paid_out
   │           │             │        │ ▲                    │           ▲
   ▼           ▼             ▼        │ └── disputed ──┐     ▼           │
 voided      voided       refunded    ├──────► partially_refunded   payout_failed
                                      └──────► refunded   (then released)  (retry)
```

### Ledger

| Account | Type | Meaning |
| --- | --- | --- |
| `provider_cash` | asset | Money we hold at the payment provider |
| `host_payable` | liability | What we owe each host |
| `platform_revenue` | revenue | Our fees |

| Event (USD 500 at a 15% fee) | Debit | Credit |
| --- | --- | --- |
| Capture | provider_cash 500.00 | host_payable 425.00, platform_revenue 75.00 |
| Partial refund of 100.00 | host_payable 85.00, platform_revenue 15.00 | provider_cash 100.00 |
| Payout | host_payable 340.00 | provider_cash 340.00 |

The invariant `provider_cash == host_payable + platform_revenue` is checked by the randomized tests and by every reconciliation run.

### AI agents

| Agent | Decides | Model's role | Fallback with no model |
| --- | --- | --- | --- |
| Risk | Rules (7 weighted signals, two thresholds) | Explains the score in plain English | Template explanation |
| Dispute mediator | An operator | Recommends a refund, citing evidence; clamped to the claim | Evidence-weight split |
| Reconciliation | Deterministic checks | Summarises findings for on-call | Severity-ordered template |
| Host assistant | Nothing (read-only) | Answers from the host's own records only | Keyword intents |

Prompts live in `src/settle/ai/prompts/` with a version line; every AI output is stored with the model and prompt version that produced it. `settle eval-disputes` scores the mediator against eight hand-labelled cases. The rule-based baseline gets 6/8; that's the bar a model has to beat.

## Project structure

```
settle/
├── src/settle/
│   ├── domain/        Money, fees and refund splits, policies, FX, state machine (no I/O)
│   ├── ledger/        Chart of accounts, posting recipes, balanced posting
│   ├── db/            Engine/session, portable column types, ORM models
│   ├── providers/     PaymentProvider contract, FakeProvider, PayPalProvider
│   ├── outbox/        Transactional outbox and retrying relay
│   ├── idempotency/   Idempotency-key store
│   ├── ai/            LLM client, agents, versioned prompts, eval set
│   ├── services/      Use cases: bookings, payments, refunds, payouts, disputes, risk,
│   │                  webhooks, reconciliation, worker handlers, access rules
│   ├── api/           FastAPI app, routers, schemas, error mapping
│   ├── workers/       Scheduled jobs and the standalone worker
│   ├── migrations/    Alembic migrations (shipped inside the package)
│   ├── auth.py        Password hashing and tokens
│   ├── bootstrap.py   Composition root: wires everything in one place
│   ├── config.py      Settings from SETTLE_* environment variables
│   ├── demo.py        The end-to-end story
│   └── cli.py         settle migrate | seed | demo | reconcile | eval-disputes
├── web/               React + TypeScript + Tailwind frontend
├── tests/             unit/ and integration/ (187 tests)
├── render.yaml        One-click deploy to Render (free tier)
└── .github/workflows/ CI: lint, mypy --strict, tests on SQLite and Postgres, frontend build
```

Dependencies point inward: `domain` imports nothing from the app; only `api` and `workers` know about FastAPI and APScheduler.

## Getting started

Requirements: **Python 3.11+** and **Node 18+**. No Docker, Redis or database server; local development uses one SQLite file.

**Windows (PowerShell)**

```powershell
git clone https://github.com/Kingsmichaei/settle.git
cd settle
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
copy .env.example .env

settle migrate
settle seed
uvicorn settle.api.app:create_app --factory --reload
```

In a second terminal:

```powershell
cd web
npm install
npm run dev
```

Open **http://localhost:5173** and pick an account. Every demo account's password is `settle-demo`:

| Account | Role | Try |
| --- | --- | --- |
| ada.guest@example.com | Guest | Book a stay, approve the sandbox payment, cancel it, or report a problem |
| joao.host@example.com | Host (Lisbon, paid in EUR) | Watch payouts; ask "When is my next payout?" |
| chioma.host@example.com | Host (Lagos, paid in NGN) | Same, in another currency |
| ops@example.com | Admin | Review risk, ask the mediator, decide disputes, run reconciliation |

On macOS or Linux, use `source .venv/bin/activate` and `cp .env.example .env`. The API docs are at http://localhost:8000/docs.

To serve everything from one process (as in production), run `npm run build` in `web/`, set `SETTLE_PUBLIC_BASE_URL=http://localhost:8000` in `.env`, then open http://localhost:8000.

### Using the real PayPal sandbox

1. Create an app at [developer.paypal.com](https://developer.paypal.com) and copy its client id and secret.
2. Add a webhook pointing at `https://<your-host>/v1/webhooks/paypal` for `CHECKOUT.ORDER.APPROVED`, `PAYMENT.CAPTURE.REVERSED` and `PAYMENT.PAYOUTS-ITEM.*`, and copy its id.
3. In `.env`, set `SETTLE_PAYMENT_PROVIDER=paypal` and the three `SETTLE_PAYPAL_*` values.

Locally, webhooks need a public URL (for example a free tunnel); the guest's return link confirms payments even without one.

### Using Gemini for the AI agents

1. Create a free API key in [Google AI Studio](https://aistudio.google.com/apikey).
2. In `.env`, set `SETTLE_AI_PROVIDER=gemini` and `SETTLE_GEMINI_API_KEY=<your key>`.
3. Optionally change the model with `SETTLE_AI_MODEL` (default `gemini-3.5-flash-lite`, the cheapest current model).
4. Check it with `settle eval-disputes`: the last line names the mediator, e.g. `gemini:gemini-3.5-flash-lite/mediator@...`.

Everything also works without a key, on the rule-based fallbacks. If Gemini is down or returns something invalid, each agent falls back to its rules for that call.

## Testing

```bash
pytest               # 187 tests, about 30 seconds
ruff check src tests
mypy                 # strict
settle demo
settle eval-disputes
```

* **Failure injection:** provider timeouts after success, replayed events, duplicate webhooks, webhooks arriving early, retries exhausting, a cancellation racing a capture, concurrent writers.
* **Randomized invariants:** Hypothesis builds batches of bookings across all three policies with random cancellations, disputes and provider faults, then requires balanced books and zero critical reconciliation findings.
* **Property tests:** money, fee and refund splits, and every ledger recipe, for any amount and fee rate.
* **PayPal adapter:** request shapes, `PayPal-Request-Id`, token refresh, error mapping and webhook parsing, against a scripted PayPal API.
* **Security:** password hashing, token expiry and tampering, role and ownership checks, per-user idempotency scopes.
* **Migrations** must produce exactly the schema the models describe.

Set `SETTLE_TEST_DATABASE_URL=postgresql+psycopg://...` to run against Postgres; CI does this on every push.

## Deploying

`render.yaml` deploys the API, the built frontend and an embedded worker as one free web service with a free Postgres database. Fill in the PayPal, Gemini and URL settings in the Render dashboard. At scale, run the worker as its own service (`python -m settle.workers`).

## Notes and limits

* The fake provider keeps its state in memory, so after a restart reconciliation can't verify older captures with it. PayPal has no such limit.
* Disputes are refund claims made while funds are held. Claims after the host is paid (host damage claims, chargeback evidence) are out of scope.
* FX rates come from configuration; a live rates feed would plug in behind the `FxRates` interface.

## License

MIT, see [LICENSE](LICENSE).
