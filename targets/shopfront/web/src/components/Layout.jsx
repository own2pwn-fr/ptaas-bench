/**
 * Site chrome: the promotional strip, the header, the footer and the cookie notice.
 *
 * The trading name comes from the shell's `window.__STORE__`, never from a constant in
 * the bundle: the same build serves several estates and each has its own name.
 */
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";

import { api, useResource } from "../lib/api.js";
import { useCart } from "../lib/cart.jsx";
import { useSession } from "../lib/session.jsx";
import { listOf, storeInfo } from "../lib/store.js";
import ConsentBanner from "./ConsentBanner.jsx";
import SearchBox from "./SearchBox.jsx";

function PromoStrip() {
  const { data } = useResource(({ signal }) => api.get("/api/content/banners", null, { signal }), []);
  const banners = listOf(data, "banners");
  const first = banners[0];
  if (!first) return null;
  return (
    <div className="promo" role="note">
      {first.headline ?? first.title ?? first.message}
      {first.href ? (
        <>
          {" "}
          <Link to={first.href} data-track="promo-link">
            {first.cta ?? "Find out more"}
          </Link>
        </>
      ) : null}
    </div>
  );
}

function Footer() {
  const store = storeInfo();
  const { data: currencies } = useResource(({ signal }) => api.get("/api/currencies", null, { signal }), []);
  const { data: locales } = useResource(({ signal }) => api.get("/api/locales", null, { signal }), []);
  const currencyList = listOf(currencies, "currencies");
  const localeList = listOf(locales, "locales");

  return (
    <footer className="site-footer">
      <div className="shell footer-grid">
        <div>
          <p className="footer-title">{store.name}</p>
          <p className="muted small">
            Hard-wearing outdoor and household goods, made to be repaired rather than replaced.
          </p>
        </div>
        <nav aria-label="Shop">
          <p className="footer-title">Shop</p>
          <ul className="plain">
            <li><Link to="/catalog">Everything</Link></li>
            <li><Link to="/gift-cards">Gift cards</Link></li>
            <li><Link to="/stores">Shops near you</Link></li>
            <li><Link to="/search">Search</Link></li>
          </ul>
        </nav>
        <nav aria-label="Help">
          <p className="footer-title">Help</p>
          <ul className="plain">
            <li><Link to="/support">Help centre</Link></li>
            <li><Link to="/pages/delivery">Delivery</Link></li>
            <li><Link to="/pages/returns">Returns</Link></li>
            <li><Link to="/pages/repairs">Repairs</Link></li>
          </ul>
        </nav>
        <nav aria-label="About">
          <p className="footer-title">About</p>
          <ul className="plain">
            <li><Link to="/pages/about">Our story</Link></li>
            <li><Link to="/pages/terms">Terms</Link></li>
            <li><Link to="/pages/privacy">Privacy</Link></li>
            <li><Link to="/pages/cookies">Cookies</Link></li>
          </ul>
        </nav>
      </div>
      <div className="shell footer-meta">
        <span className="muted small">
          Prices in {store.currency}
          {currencyList.length > 1 ? ` · ${currencyList.length} currencies supported` : ""}
          {localeList.length > 1 ? ` · ${localeList.length} languages` : ""}
        </span>
        <span className="muted small">© {new Date().getFullYear()} {store.name}</span>
      </div>
    </footer>
  );
}

export default function Layout() {
  const store = storeInfo();
  const { count } = useCart();
  const { signedIn, user, signOut } = useSession();
  const navigate = useNavigate();

  const leave = async () => {
    await signOut();
    navigate("/");
  };

  return (
    <div className="app">
      <a className="skip" href="#main">Skip to content</a>
      <PromoStrip />
      <header className="site-header">
        <div className="shell header-grid">
          <Link className="brand" to="/" data-track="brand">
            <span className="brand-mark" aria-hidden="true" />
            <span className="brand-name">{store.name}</span>
          </Link>
          <SearchBox />
          <nav className="header-links" aria-label="Account">
            {signedIn ? (
              <>
                <NavLink to="/account" data-track="nav-account">
                  {user?.first_name ?? user?.name ?? "My account"}
                </NavLink>
                <button type="button" className="linklike" onClick={leave} data-track="nav-sign-out">
                  Sign out
                </button>
              </>
            ) : (
              <>
                <NavLink to="/sign-in" data-track="nav-sign-in">Sign in</NavLink>
                <NavLink to="/sign-up">Create account</NavLink>
              </>
            )}
            <NavLink to="/cart" className="cart-link" data-track="nav-cart">
              Basket{count ? <span className="cart-count">{count}</span> : null}
            </NavLink>
          </nav>
        </div>
        <nav className="site-nav" aria-label="Main">
          <div className="shell nav-row">
            <NavLink to="/catalog">Catalogue</NavLink>
            <NavLink to="/catalog/outdoor">Outdoor</NavLink>
            <NavLink to="/catalog/kitchen">Kitchen</NavLink>
            <NavLink to="/catalog/garden">Garden</NavLink>
            <NavLink to="/gift-cards">Gift cards</NavLink>
            <NavLink to="/stores">Shops</NavLink>
            <NavLink to="/support">Help</NavLink>
          </div>
        </nav>
      </header>
      <main id="main" className="shell site-main">
        <Outlet />
      </main>
      <Footer />
      <ConsentBanner />
    </div>
  );
}
