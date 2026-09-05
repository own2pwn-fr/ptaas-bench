/**
 * Routing.
 *
 * Every page route the shell serves has an entry here; the shell answers all of them
 * with the same document, so a deep link only works if the table below matches it.
 */
import { createBrowserRouter, RouterProvider } from "react-router-dom";

import Layout from "./components/Layout.jsx";
import { CartProvider } from "./lib/cart.jsx";
import { SessionProvider } from "./lib/session.jsx";
import Cart from "./pages/Cart.jsx";
import Catalog from "./pages/Catalog.jsx";
import Checkout from "./pages/Checkout.jsx";
import ContentPage from "./pages/ContentPage.jsx";
import GiftCards from "./pages/GiftCards.jsx";
import Home from "./pages/Home.jsx";
import NotFound from "./pages/NotFound.jsx";
import Product from "./pages/Product.jsx";
import Search from "./pages/Search.jsx";
import SignIn from "./pages/SignIn.jsx";
import SignUp from "./pages/SignUp.jsx";
import Stores from "./pages/Stores.jsx";
import Support from "./pages/Support.jsx";
import SupportTicket from "./pages/SupportTicket.jsx";
import AccountLayout from "./pages/account/AccountLayout.jsx";
import Addresses from "./pages/account/Addresses.jsx";
import Dashboard from "./pages/account/Dashboard.jsx";
import Loyalty from "./pages/account/Loyalty.jsx";
import OrderDetail from "./pages/account/OrderDetail.jsx";
import Orders from "./pages/account/Orders.jsx";
import PaymentMethods from "./pages/account/PaymentMethods.jsx";
import Preferences from "./pages/account/Preferences.jsx";
import Profile from "./pages/account/Profile.jsx";
import SavedSearches from "./pages/account/SavedSearches.jsx";
import Wallet from "./pages/account/Wallet.jsx";
import Wishlist from "./pages/account/Wishlist.jsx";
import AdminCoupons from "./pages/admin/AdminCoupons.jsx";
import AdminHome from "./pages/admin/AdminHome.jsx";
import AdminImports from "./pages/admin/AdminImports.jsx";
import AdminLayout from "./pages/admin/AdminLayout.jsx";
import AdminOrders from "./pages/admin/AdminOrders.jsx";
import AdminSupport from "./pages/admin/AdminSupport.jsx";

const router = createBrowserRouter([
  {
    element: <Layout />,
    children: [
      { path: "/", element: <Home /> },
      { path: "/catalog", element: <Catalog /> },
      { path: "/catalog/:slug", element: <Catalog /> },
      { path: "/product/:slug", element: <Product /> },
      { path: "/search", element: <Search /> },
      { path: "/stores", element: <Stores /> },
      { path: "/gift-cards", element: <GiftCards /> },
      { path: "/pages/:slug", element: <ContentPage /> },
      { path: "/support", element: <Support /> },
      { path: "/support/tickets/:id", element: <SupportTicket /> },
      { path: "/cart", element: <Cart /> },
      { path: "/checkout", element: <Checkout /> },
      { path: "/sign-in", element: <SignIn /> },
      { path: "/sign-up", element: <SignUp /> },
      {
        path: "/account",
        element: <AccountLayout />,
        children: [
          { index: true, element: <Dashboard /> },
          { path: "orders", element: <Orders /> },
          { path: "orders/:id", element: <OrderDetail /> },
          { path: "addresses", element: <Addresses /> },
          { path: "payment-methods", element: <PaymentMethods /> },
          { path: "preferences", element: <Preferences /> },
          { path: "saved-searches", element: <SavedSearches /> },
          { path: "wishlist", element: <Wishlist /> },
          { path: "wallet", element: <Wallet /> },
          { path: "loyalty", element: <Loyalty /> },
          { path: "profile", element: <Profile /> },
        ],
      },
      {
        path: "/admin",
        element: <AdminLayout />,
        children: [
          { index: true, element: <AdminHome /> },
          { path: "orders", element: <AdminOrders /> },
          { path: "coupons", element: <AdminCoupons /> },
          { path: "imports", element: <AdminImports /> },
          { path: "support", element: <AdminSupport /> },
        ],
      },
      { path: "*", element: <NotFound /> },
    ],
  },
]);

export default function App() {
  return (
    <SessionProvider>
      <CartProvider>
        <RouterProvider router={router} />
      </CartProvider>
    </SessionProvider>
  );
}
