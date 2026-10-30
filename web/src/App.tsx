import type { ReactNode } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";

import type { Role } from "./api/types";
import { homeFor, useAuth } from "./auth/AuthContext";
import { AppShell } from "./components/AppShell";
import { Loading, Page } from "./components/ui";
import { DisputePage } from "./pages/DisputePage";
import { EarningsPage } from "./pages/Earnings";
import { LoginPage } from "./pages/Login";
import { OpsPage } from "./pages/Ops";
import { PaymentReturnPage, SandboxApprovePage } from "./pages/Sandbox";
import { StayDetailPage } from "./pages/StayDetail";
import { StaysPage } from "./pages/Stays";
import { TripDetailPage } from "./pages/TripDetail";
import { TripsPage } from "./pages/Trips";

function RequireAuth({ roles, children }: { roles?: Role[]; children: ReactNode }) {
  const { user, loading } = useAuth();
  const location = useLocation();
  if (loading) return <Page title=""><Loading /></Page>;
  if (!user) return <Navigate to="/login" replace state={{ from: location }} />;
  if (roles && !roles.includes(user.role)) return <Navigate to={homeFor(user.role)} replace />;
  return <>{children}</>;
}

function Home() {
  const { user, loading } = useAuth();
  if (loading) return null;
  return <Navigate to={user ? homeFor(user.role) : "/login"} replace />;
}

export function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Home />} />
        <Route path="login" element={<LoginPage />} />
        <Route path="stays" element={<RequireAuth roles={["guest"]}><StaysPage /></RequireAuth>} />
        <Route path="stays/:listingId" element={<RequireAuth roles={["guest"]}><StayDetailPage /></RequireAuth>} />
        <Route path="trips" element={<RequireAuth roles={["guest"]}><TripsPage /></RequireAuth>} />
        <Route path="trips/:bookingId" element={<RequireAuth><TripDetailPage /></RequireAuth>} />
        <Route path="trips/:bookingId/return" element={<RequireAuth roles={["guest"]}><PaymentReturnPage /></RequireAuth>} />
        <Route path="disputes/:disputeId" element={<RequireAuth><DisputePage /></RequireAuth>} />
        <Route path="earnings" element={<RequireAuth roles={["host"]}><EarningsPage /></RequireAuth>} />
        <Route path="ops" element={<RequireAuth roles={["admin"]}><OpsPage /></RequireAuth>} />
        <Route path="*" element={<Page title="Page not found" intro="That link doesn't lead anywhere. Use the menu above." >{null}</Page>} />
      </Route>
      <Route path="sandbox/approve/:orderId" element={<RequireAuth roles={["guest"]}><SandboxApprovePage /></RequireAuth>} />
    </Routes>
  );
}
