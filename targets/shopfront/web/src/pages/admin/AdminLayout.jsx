import { NavLink, Outlet } from "react-router-dom";

import { Loading } from "../../components/ui.jsx";
import { useSession } from "../../lib/session.jsx";
import { storeName } from "../../lib/store.js";

const LINKS = [
  ["/admin", "Today", true],
  ["/admin/orders", "Orders"],
  ["/admin/coupons", "Discount codes"],
  ["/admin/imports", "Catalogue imports"],
  ["/admin/support", "Customer service"],
];

/** The back office. Small on purpose: the warehouse system owns everything else. */
export default function AdminLayout() {
  const { ready, user } = useSession();

  if (!ready) return <Loading label="Checking your session…" />;

  return (
    <div className="two-col">
      <aside className="side">
        <p className="side-title">{storeName()} desk</p>
        <ul className="plain side-list">
          {LINKS.map(([to, label, end]) => (
            <li key={to}>
              <NavLink to={to} end={Boolean(end)} className={({ isActive }) => (isActive ? "current" : "")}>
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
        <p className="muted small">Signed in as {user?.email ?? "staff"}</p>
      </aside>
      <div>
        <Outlet />
      </div>
    </div>
  );
}
