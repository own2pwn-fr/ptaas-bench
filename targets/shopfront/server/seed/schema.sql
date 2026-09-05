-- Storefront schema.
--
-- Rebuilt from scratch by bin/state-reset, so it is written to be dropped and recreated
-- rather than migrated. The production estate runs the same file through the ordinary
-- migration runner; this copy is what the container ships so a fresh instance can come
-- up against an empty database.
--
-- Money is stored in minor units in INTEGER columns, inherited from the original ledger,
-- which predates the move to NUMERIC in the finance service.

DROP SCHEMA IF EXISTS public CASCADE;
CREATE SCHEMA public;

CREATE TABLE customers (
  id              INTEGER PRIMARY KEY,
  email           TEXT NOT NULL UNIQUE,
  password_hash   TEXT NOT NULL,
  password_salt   TEXT NOT NULL,
  given_name      TEXT NOT NULL,
  family_name     TEXT NOT NULL,
  display_name    TEXT NOT NULL,
  phone           TEXT,
  role            TEXT NOT NULL DEFAULT 'customer',
  loyalty_tier    TEXT NOT NULL DEFAULT 'bronze',
  loyalty_points  INTEGER NOT NULL DEFAULT 0,
  avatar_url      TEXT,
  marketing_opt_in BOOLEAN NOT NULL DEFAULT false,
  created_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE addresses (
  id           INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(id),
  label        TEXT NOT NULL,
  recipient    TEXT NOT NULL,
  line1        TEXT NOT NULL,
  line2        TEXT,
  city         TEXT NOT NULL,
  postcode     TEXT NOT NULL,
  country      TEXT NOT NULL,
  is_default   BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE payment_methods (
  id           INTEGER PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(id),
  brand        TEXT NOT NULL,
  last4        TEXT NOT NULL,
  exp_month    INTEGER NOT NULL,
  exp_year     INTEGER NOT NULL,
  is_default   BOOLEAN NOT NULL DEFAULT false
);

CREATE TABLE categories (
  id       INTEGER PRIMARY KEY,
  slug     TEXT NOT NULL UNIQUE,
  name     TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE brands (
  id    INTEGER PRIMARY KEY,
  slug  TEXT NOT NULL UNIQUE,
  name  TEXT NOT NULL,
  blurb TEXT NOT NULL
);

CREATE TABLE products (
  id           INTEGER PRIMARY KEY,
  slug         TEXT NOT NULL UNIQUE,
  title        TEXT NOT NULL,
  summary      TEXT NOT NULL,
  description  TEXT NOT NULL,
  category_id  INTEGER NOT NULL REFERENCES categories(id),
  brand_id     INTEGER NOT NULL REFERENCES brands(id),
  price_cents  INTEGER NOT NULL,
  currency     TEXT NOT NULL DEFAULT 'EUR',
  rating_avg   NUMERIC(3,2) NOT NULL DEFAULT 0,
  rating_count INTEGER NOT NULL DEFAULT 0,
  tag          TEXT NOT NULL DEFAULT 'general',
  is_active    BOOLEAN NOT NULL DEFAULT true,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE variants (
  id           INTEGER PRIMARY KEY,
  product_id   INTEGER NOT NULL REFERENCES products(id),
  sku          TEXT NOT NULL UNIQUE,
  option_name  TEXT NOT NULL,
  option_value TEXT NOT NULL,
  price_cents  INTEGER NOT NULL,
  stock        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE media (
  id         INTEGER PRIMARY KEY,
  product_id INTEGER NOT NULL REFERENCES products(id),
  url        TEXT NOT NULL,
  alt        TEXT NOT NULL,
  position   INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE reviews (
  id          INTEGER PRIMARY KEY,
  product_id  INTEGER NOT NULL REFERENCES products(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  rating      INTEGER NOT NULL,
  title       TEXT NOT NULL,
  body        TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'published',
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE carts (
  id          INTEGER PRIMARY KEY,
  token       TEXT NOT NULL UNIQUE,
  customer_id INTEGER REFERENCES customers(id),
  currency    TEXT NOT NULL DEFAULT 'EUR',
  meta        JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at  TIMESTAMPTZ NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE cart_items (
  id               INTEGER PRIMARY KEY,
  cart_id          INTEGER NOT NULL REFERENCES carts(id),
  variant_id       INTEGER NOT NULL REFERENCES variants(id),
  quantity         INTEGER NOT NULL,
  unit_price_cents INTEGER NOT NULL,
  added_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE coupons (
  id               INTEGER PRIMARY KEY,
  code             TEXT NOT NULL UNIQUE,
  description      TEXT NOT NULL,
  percent_off      INTEGER,
  amount_off_cents INTEGER,
  max_redemptions  INTEGER NOT NULL DEFAULT 1,
  redemptions      INTEGER NOT NULL DEFAULT 0,
  is_active        BOOLEAN NOT NULL DEFAULT true,
  expires_at       TIMESTAMPTZ,
  created_by       INTEGER REFERENCES customers(id),
  created_at       TIMESTAMPTZ NOT NULL
);

CREATE TABLE checkout_sessions (
  id                  INTEGER PRIMARY KEY,
  cart_id             INTEGER NOT NULL REFERENCES carts(id),
  customer_id         INTEGER NOT NULL REFERENCES customers(id),
  address_id          INTEGER REFERENCES addresses(id),
  payment_method_id   INTEGER REFERENCES payment_methods(id),
  shipping_method     TEXT,
  shipping_rate_cents INTEGER,
  state               TEXT NOT NULL DEFAULT 'open',
  created_at          TIMESTAMPTZ NOT NULL
);

CREATE TABLE checkout_coupons (
  id         INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES checkout_sessions(id),
  code       TEXT NOT NULL,
  applied_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE orders (
  id             INTEGER PRIMARY KEY,
  reference      TEXT NOT NULL UNIQUE,
  customer_id    INTEGER NOT NULL REFERENCES customers(id),
  address_id     INTEGER REFERENCES addresses(id),
  state          TEXT NOT NULL DEFAULT 'placed',
  currency       TEXT NOT NULL DEFAULT 'EUR',
  subtotal_cents INTEGER NOT NULL,
  shipping_cents INTEGER NOT NULL DEFAULT 0,
  discount_cents INTEGER NOT NULL DEFAULT 0,
  total_cents    INTEGER NOT NULL,
  placed_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE order_items (
  id               INTEGER PRIMARY KEY,
  order_id         INTEGER NOT NULL REFERENCES orders(id),
  variant_id       INTEGER NOT NULL REFERENCES variants(id),
  title            TEXT NOT NULL,
  quantity         INTEGER NOT NULL,
  unit_price_cents INTEGER NOT NULL,
  line_total_cents INTEGER NOT NULL
);

CREATE TABLE order_transitions (
  id             INTEGER PRIMARY KEY,
  order_id       INTEGER NOT NULL REFERENCES orders(id),
  from_state     TEXT NOT NULL,
  to_state       TEXT NOT NULL,
  actor_subject  TEXT,
  actor_role     TEXT,
  created_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE shipments (
  id           INTEGER PRIMARY KEY,
  order_id     INTEGER NOT NULL REFERENCES orders(id),
  carrier      TEXT NOT NULL,
  tracking_ref TEXT NOT NULL,
  state        TEXT NOT NULL,
  shipped_at   TIMESTAMPTZ
);

CREATE TABLE order_returns (
  id         INTEGER PRIMARY KEY,
  order_id   INTEGER NOT NULL REFERENCES orders(id),
  reason     TEXT NOT NULL,
  state      TEXT NOT NULL DEFAULT 'requested',
  created_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE coupon_redemptions (
  id          INTEGER PRIMARY KEY,
  coupon_id   INTEGER NOT NULL REFERENCES coupons(id),
  order_id    INTEGER REFERENCES orders(id),
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  code        TEXT NOT NULL,
  redeemed_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE support_tickets (
  id          INTEGER PRIMARY KEY,
  reference   TEXT NOT NULL UNIQUE,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  subject     TEXT NOT NULL,
  status      TEXT NOT NULL DEFAULT 'open',
  priority    TEXT NOT NULL DEFAULT 'normal',
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE support_messages (
  id             INTEGER PRIMARY KEY,
  ticket_id      INTEGER NOT NULL REFERENCES support_tickets(id),
  author_kind    TEXT NOT NULL,
  author_subject TEXT,
  body           TEXT NOT NULL,
  created_at     TIMESTAMPTZ NOT NULL
);

CREATE TABLE support_articles (
  id       INTEGER PRIMARY KEY,
  slug     TEXT NOT NULL UNIQUE,
  title    TEXT NOT NULL,
  category TEXT NOT NULL,
  body     TEXT NOT NULL
);

CREATE TABLE gift_cards (
  id                INTEGER PRIMARY KEY,
  code              TEXT NOT NULL UNIQUE,
  face_value_cents  INTEGER NOT NULL,
  customer_id       INTEGER REFERENCES customers(id),
  state             TEXT NOT NULL DEFAULT 'issued',
  issued_at         TIMESTAMPTZ NOT NULL
);

CREATE TABLE wallet_credits (
  id            INTEGER PRIMARY KEY,
  customer_id   INTEGER NOT NULL REFERENCES customers(id),
  gift_card_id  INTEGER REFERENCES gift_cards(id),
  amount_cents  INTEGER NOT NULL,
  memo          TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE wishlists (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  name        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE wishlist_items (
  id          INTEGER PRIMARY KEY,
  wishlist_id INTEGER NOT NULL REFERENCES wishlists(id),
  variant_id  INTEGER NOT NULL REFERENCES variants(id),
  added_at    TIMESTAMPTZ NOT NULL
);

CREATE TABLE saved_searches (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  label       TEXT NOT NULL,
  rule        TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE notifications (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  kind        TEXT NOT NULL,
  body        TEXT NOT NULL,
  read_at     TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE account_preferences (
  customer_id INTEGER PRIMARY KEY REFERENCES customers(id),
  locale      TEXT NOT NULL DEFAULT 'en-GB',
  currency    TEXT NOT NULL DEFAULT 'EUR',
  theme       TEXT NOT NULL DEFAULT 'system',
  widgets     JSONB NOT NULL DEFAULT '[]'::jsonb,
  updated_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE loyalty_transactions (
  id          INTEGER PRIMARY KEY,
  customer_id INTEGER NOT NULL REFERENCES customers(id),
  points      INTEGER NOT NULL,
  reason      TEXT NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL
);

CREATE TABLE sessions (
  sid           TEXT PRIMARY KEY,
  customer_id   INTEGER NOT NULL REFERENCES customers(id),
  created_at    TIMESTAMPTZ NOT NULL,
  expires_at    TIMESTAMPTZ NOT NULL,
  step_up_until TIMESTAMPTZ,
  user_agent    TEXT
);

CREATE TABLE step_up_requests (
  id           TEXT PRIMARY KEY,
  customer_id  INTEGER NOT NULL REFERENCES customers(id),
  sid          TEXT NOT NULL,
  code         TEXT NOT NULL,
  purpose      TEXT NOT NULL,
  attempts     INTEGER NOT NULL DEFAULT 0,
  verified_at  TIMESTAMPTZ,
  expires_at   TIMESTAMPTZ NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE imports (
  id           INTEGER PRIMARY KEY,
  source_url   TEXT NOT NULL,
  state        TEXT NOT NULL DEFAULT 'queued',
  requested_by INTEGER REFERENCES customers(id),
  rows_seen    INTEGER NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL
);

CREATE TABLE stores (
  id      INTEGER PRIMARY KEY,
  slug    TEXT NOT NULL UNIQUE,
  name    TEXT NOT NULL,
  city    TEXT NOT NULL,
  street  TEXT NOT NULL,
  phone   TEXT NOT NULL
);

CREATE TABLE store_hours (
  id       INTEGER PRIMARY KEY,
  store_id INTEGER NOT NULL REFERENCES stores(id),
  weekday  INTEGER NOT NULL,
  opens    TEXT NOT NULL,
  closes   TEXT NOT NULL
);

CREATE TABLE content_pages (
  id         INTEGER PRIMARY KEY,
  slug       TEXT NOT NULL UNIQUE,
  title      TEXT NOT NULL,
  body       TEXT NOT NULL,
  updated_at TIMESTAMPTZ NOT NULL
);

CREATE TABLE banners (
  id       INTEGER PRIMARY KEY,
  slug     TEXT NOT NULL UNIQUE,
  headline TEXT NOT NULL,
  body     TEXT NOT NULL,
  cta_url  TEXT NOT NULL,
  position INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE newsletter_subscriptions (
  id         INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  email      TEXT NOT NULL,
  locale     TEXT NOT NULL DEFAULT 'en-GB',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE contact_messages (
  id         INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  name       TEXT NOT NULL,
  email      TEXT NOT NULL,
  body       TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE audit_log (
  id            INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  actor_subject TEXT,
  action        TEXT NOT NULL,
  detail        TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE client_events (
  id         INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  kind       TEXT NOT NULL,
  route      TEXT,
  detail     JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Report-only content security policy violations. The rollout plan is to watch this
-- table for a month, fix the inline scripts it lists, then enforce the policy.
CREATE TABLE policy_reports (
  id           INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  document_uri TEXT NOT NULL,
  directive    TEXT NOT NULL,
  blocked_uri  TEXT,
  sample       TEXT,
  client_ip    TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Counters the storefront keeps for its own dashboards; identity sequences below start
-- past the seeded block so a reset and a running instance never collide on an id.
CREATE TABLE id_counters (
  name  TEXT PRIMARY KEY,
  value INTEGER NOT NULL
);

CREATE INDEX products_category_idx ON products(category_id);
CREATE INDEX variants_product_idx ON variants(product_id);
CREATE INDEX reviews_product_idx ON reviews(product_id);
CREATE INDEX orders_customer_idx ON orders(customer_id);
CREATE INDEX order_items_order_idx ON order_items(order_id);
CREATE INDEX tickets_customer_idx ON support_tickets(customer_id);
CREATE INDEX messages_ticket_idx ON support_messages(ticket_id);
CREATE INDEX cart_items_cart_idx ON cart_items(cart_id);
CREATE INDEX sessions_customer_idx ON sessions(customer_id);
