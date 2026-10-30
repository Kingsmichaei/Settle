import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router-dom";

import { homeFor, useAuth } from "../auth/AuthContext";
import { Button, ErrorNote, Field } from "../components/ui";
import { useAction } from "../lib/useAsync";

const DEMO_PASSWORD = "settle-demo";
const DEMO_ACCOUNTS = [
  { email: "ada.guest@example.com", name: "Ada", role: "Guest", does: "Books a stay and pays with the sandbox" },
  { email: "joao.host@example.com", name: "Joao", role: "Host in Lisbon", does: "Sees payouts and asks the earnings assistant" },
  { email: "ops@example.com", name: "Operations", role: "Admin", does: "Reviews risk, decides disputes, reconciles" },
];

export function LoginPage() {
  const { user, signIn } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const { run, busy, error } = useAction(async (address: string, secret: string) => {
    const signedIn = await signIn(address, secret);
    navigate(homeFor(signedIn.role));
  });

  if (user) return <Navigate to={homeFor(user.role)} replace />;

  const submit = (event: FormEvent) => {
    event.preventDefault();
    void run(email, password);
  };

  return (
    <main className="mx-auto grid max-w-[1080px] gap-12 px-4 py-12 sm:px-6 lg:grid-cols-[1.1fr_1fr]">
      <div>
        <h1 className="text-3xl font-bold tracking-tight">Payments for stays, from booking to payout.</h1>
        <p className="mt-4 max-w-xl text-lg text-ink-muted">
          Settle charges guests, holds the money until 24 hours after check-in, then pays hosts in their own
          currency. Pick an account to see it from that side.
        </p>
        <ul className="mt-8 divide-y divide-line rounded-xl border border-line bg-white">
          {DEMO_ACCOUNTS.map((account) => (
            <li key={account.email}>
              <button
                type="button"
                disabled={busy}
                onClick={() => void run(account.email, DEMO_PASSWORD)}
                className="flex w-full items-center gap-4 px-5 py-4 text-left hover:bg-tile/60 disabled:opacity-60"
              >
                <span className="grid h-10 w-10 shrink-0 place-items-center rounded-md bg-tile font-bold text-cobalt">
                  {account.name[0]}
                </span>
                <span>
                  <span className="block font-semibold">
                    Continue as {account.name} <span className="font-normal text-ink-muted">({account.role})</span>
                  </span>
                  <span className="block text-sm text-ink-muted">{account.does}</span>
                </span>
              </button>
            </li>
          ))}
        </ul>
      </div>

      <form onSubmit={submit} className="self-start rounded-xl border border-line bg-white p-6">
        <h2 className="text-lg font-semibold">Sign in with email</h2>
        <div className="mt-4 space-y-4">
          <Field label="Email" type="email" autoComplete="email" required value={email}
            onChange={(e) => setEmail(e.target.value)} />
          <Field label="Password" type="password" autoComplete="current-password" required value={password}
            onChange={(e) => setPassword(e.target.value)} hint={`Demo accounts use “${DEMO_PASSWORD}”.`} />
          <ErrorNote>{error}</ErrorNote>
          <Button type="submit" busy={busy} className="w-full">Sign in</Button>
        </div>
      </form>
    </main>
  );
}
