import { Link, NavLink, Outlet } from "react-router-dom";

import { Loading } from "../../components/ui.jsx";
import { useSession } from "../../lib/session.jsx";

const LINKS = [
  ["/account", "Overview", true],
  ["/account/orders", "Orders"],
  ["/account/wishlist", "Saved items"],
  ["/account/saved-searches", "Saved searches"],
  ["/account/addresses", "Addresses"],
  ["/account/payment-methods", "Payment methods"],
  ["/account/wallet", "Wallet"],
  ["/account/loyalty", "Rewards"],
  ["/account/preferences", "Preferences"],
  ["/account/profile", "Profile"],
];

export default function AccountLayout() {
  const { ready, signedIn, user } = useSession();

  if (!ready) return <Loading label="Checking your session…" />;

  if (!signedIn) {
    return (
      <div className="card stack narrow-form">
        <h1 className="card-title">Sign in to see your account</h1>
        <p className="muted">Orders, addresses, rewards and repairs all live behind the sign-in.</p>
        <Link className="btn btn-primary" to="/sign-in?next=/account">
          Sign in
        </Link>
        <Link className="btn btn-quiet" to="/sign-up">
          Create an account
        </Link>
      </div>
    );
  }

  return (
    <div className="two-col">
      <aside className="side">
        <p className="side-title">{user?.first_name ? `Hello, ${user.first_name}` : "Your account"}</p>
        <ul className="plain side-list">
          {LINKS.map(([to, label, end]) => (
            <li key={to}>
              <NavLink to={to} end={Boolean(end)} className={({ isActive }) => (isActive ? "current" : "")}>
                {label}
              </NavLink>
            </li>
          ))}
        </ul>
      </aside>
      <div>
        <Outlet />
      </div>
    </div>
  );
}
