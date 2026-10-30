import { NavLink, Outlet, useNavigate } from "react-router-dom";

import { useAuth } from "../auth/AuthContext";

const LINKS = {
  guest: [
    { to: "/stays", label: "Stays" },
    { to: "/trips", label: "My trips" },
  ],
  host: [{ to: "/earnings", label: "Earnings" }],
  admin: [{ to: "/ops", label: "Operations" }],
} as const;

export function AppShell() {
  const { user, signOut } = useAuth();
  const navigate = useNavigate();
  return (
    <div className="flex min-h-screen flex-col">
      <a href="#content" className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-10 focus:rounded focus:bg-white focus:px-3 focus:py-2">
        Skip to content
      </a>
      <header className="border-b border-line bg-white">
        <div className="mx-auto flex max-w-[1080px] flex-wrap items-center gap-x-8 gap-y-2 px-4 py-3 sm:px-6">
          <NavLink to="/" className="flex items-center gap-2 text-lg font-bold tracking-tight text-cobalt">
            <img src="/favicon.svg" alt="" className="h-6 w-6" />
            Settle
          </NavLink>
          {user && (
            <nav className="flex gap-5 text-sm font-medium" aria-label="Main">
              {LINKS[user.role].map((link) => (
                <NavLink
                  key={link.to}
                  to={link.to}
                  className={({ isActive }) =>
                    isActive ? "text-cobalt underline decoration-2 underline-offset-8" : "text-ink-muted hover:text-ink"
                  }
                >
                  {link.label}
                </NavLink>
              ))}
            </nav>
          )}
          {user && (
            <div className="ml-auto flex items-center gap-4 text-sm">
              <span className="text-ink-muted">
                {user.full_name}
                <span className="text-ink-faint"> ({user.role})</span>
              </span>
              <button
                type="button"
                className="font-medium text-cobalt hover:underline"
                onClick={() => {
                  signOut();
                  navigate("/login");
                }}
              >
                Sign out
              </button>
            </div>
          )}
        </div>
      </header>
      <div id="content" className="flex-1">
        <Outlet />
      </div>
      <footer className="border-t border-line py-6 text-center text-xs text-ink-faint">
        Settle demo. Payments run against a sandbox; no real money moves.
      </footer>
    </div>
  );
}
