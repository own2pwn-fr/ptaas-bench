-- Braithwaite Tool & Plant — the shape of the trading database.
--
-- It has grown one table at a time since 2009 and it shows: the earliest tables have no
-- foreign keys because the version of the engine in the rack at the time did not enforce
-- them on the storage engine that was in use, and nobody has been through since.

SET NAMES utf8mb4;
SET FOREIGN_KEY_CHECKS = 0;

DROP TABLE IF EXISTS order_lines;
DROP TABLE IF EXISTS orders;
DROP TABLE IF EXISTS quotes;
DROP TABLE IF EXISTS favourites;
DROP TABLE IF EXISTS order_templates;
DROP TABLE IF EXISTS documents;
DROP TABLE IF EXISTS statements;
DROP TABLE IF EXISTS addresses;
DROP TABLE IF EXISTS contacts;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS branch_stock;
DROP TABLE IF EXISTS products;
DROP TABLE IF EXISTS categories;
DROP TABLE IF EXISTS brands;
DROP TABLE IF EXISTS branches;
DROP TABLE IF EXISTS literature;
DROP TABLE IF EXISTS news;
DROP TABLE IF EXISTS vacancies;
DROP TABLE IF EXISTS applications;
DROP TABLE IF EXISTS enquiries;
DROP TABLE IF EXISTS newsletter;
DROP TABLE IF EXISTS feedback;
DROP TABLE IF EXISTS stock_alerts;
DROP TABLE IF EXISTS password_resets;
DROP TABLE IF EXISTS issued_sessions;
DROP TABLE IF EXISTS settings;
DROP TABLE IF EXISTS audit_log;

SET FOREIGN_KEY_CHECKS = 1;

CREATE TABLE categories (
  id           INT UNSIGNED NOT NULL PRIMARY KEY,
  slug         VARCHAR(60)  NOT NULL,
  name         VARCHAR(120) NOT NULL,
  blurb        VARCHAR(400) NOT NULL DEFAULT '',
  sort_order   INT          NOT NULL DEFAULT 0,
  UNIQUE KEY slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE brands (
  id           INT UNSIGNED NOT NULL PRIMARY KEY,
  slug         VARCHAR(60)  NOT NULL,
  name         VARCHAR(120) NOT NULL,
  blurb        VARCHAR(400) NOT NULL DEFAULT '',
  UNIQUE KEY slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE products (
  id            INT UNSIGNED NOT NULL PRIMARY KEY,
  reference     VARCHAR(40)  NOT NULL,
  name          VARCHAR(200) NOT NULL,
  description   TEXT         NOT NULL,
  category_id   INT UNSIGNED NOT NULL,
  brand_id      INT UNSIGNED NULL,
  price_pence   INT          NOT NULL DEFAULT 0,
  was_pence     INT          NOT NULL DEFAULT 0,
  unit          VARCHAR(30)  NOT NULL DEFAULT 'each',
  pack_size     VARCHAR(30)  NOT NULL DEFAULT '',
  stock         INT          NOT NULL DEFAULT 0,
  on_offer      TINYINT(1)   NOT NULL DEFAULT 0,
  discontinued  TINYINT(1)   NOT NULL DEFAULT 0,
  UNIQUE KEY reference (reference),
  KEY category_id (category_id),
  KEY brand_id (brand_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE branches (
  id         INT UNSIGNED NOT NULL PRIMARY KEY,
  name       VARCHAR(120) NOT NULL,
  town       VARCHAR(80)  NOT NULL,
  postcode   VARCHAR(12)  NOT NULL,
  phone      VARCHAR(30)  NOT NULL,
  opening    VARCHAR(200) NOT NULL DEFAULT '',
  manager    VARCHAR(120) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE branch_stock (
  branch_id  INT UNSIGNED NOT NULL,
  product_id INT UNSIGNED NOT NULL,
  quantity   INT NOT NULL DEFAULT 0,
  PRIMARY KEY (branch_id, product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE customers (
  id                 INT UNSIGNED NOT NULL PRIMARY KEY,
  account_code       VARCHAR(20)  NOT NULL,
  company            VARCHAR(160) NOT NULL,
  town               VARCHAR(80)  NOT NULL DEFAULT '',
  postcode           VARCHAR(12)  NOT NULL DEFAULT '',
  credit_limit_pence INT          NOT NULL DEFAULT 0,
  balance_pence      INT          NOT NULL DEFAULT 0,
  terms              VARCHAR(60)  NOT NULL DEFAULT '30 days from statement',
  UNIQUE KEY account_code (account_code)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE contacts (
  id           INT UNSIGNED NOT NULL PRIMARY KEY,
  customer_id  INT UNSIGNED NOT NULL,
  name         VARCHAR(120) NOT NULL,
  email        VARCHAR(160) NOT NULL,
  phone        VARCHAR(40)  NOT NULL DEFAULT '',
  job_title    VARCHAR(80)  NOT NULL DEFAULT '',
  -- The digest column has been this width since the first version of the site.
  password     CHAR(32)     NOT NULL,
  is_staff     TINYINT(1)   NOT NULL DEFAULT 0,
  last_seen_at DATETIME     NULL,
  UNIQUE KEY email (email),
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE addresses (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT UNSIGNED NOT NULL,
  label       VARCHAR(60)  NOT NULL,
  line1       VARCHAR(120) NOT NULL DEFAULT '',
  line2       VARCHAR(120) NOT NULL DEFAULT '',
  town        VARCHAR(60)  NOT NULL DEFAULT '',
  postcode    VARCHAR(12)  NOT NULL DEFAULT '',
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE orders (
  id                 INT UNSIGNED NOT NULL PRIMARY KEY,
  reference          VARCHAR(30)  NOT NULL,
  customer_id        INT UNSIGNED NOT NULL,
  contact_id         INT UNSIGNED NULL,
  branch_id          INT UNSIGNED NULL,
  address_id         INT UNSIGNED NULL,
  po_reference       VARCHAR(60)  NOT NULL DEFAULT '',
  placed_at          DATETIME     NOT NULL,
  total_pence        INT          NOT NULL DEFAULT 0,
  carriage_pence     INT          NOT NULL DEFAULT 0,
  carriage_cost_pence INT         NOT NULL DEFAULT 0,
  status             VARCHAR(30)  NOT NULL DEFAULT 'placed',
  UNIQUE KEY reference (reference),
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_lines (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  order_id    INT UNSIGNED NOT NULL,
  product_id  INT UNSIGNED NOT NULL,
  quantity    INT NOT NULL DEFAULT 1,
  price_pence INT NOT NULL DEFAULT 0,
  KEY order_id (order_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE quotes (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT UNSIGNED NOT NULL,
  contact_id  INT UNSIGNED NULL,
  reference   VARCHAR(80)  NOT NULL DEFAULT '',
  note        TEXT         NOT NULL,
  total_pence INT          NOT NULL DEFAULT 0,
  status      VARCHAR(20)  NOT NULL DEFAULT 'open',
  created_at  DATETIME     NOT NULL,
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE documents (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT UNSIGNED NOT NULL,
  contact_id  INT UNSIGNED NULL,
  filename    VARCHAR(200) NOT NULL,
  note        VARCHAR(400) NOT NULL DEFAULT '',
  uploaded_at DATETIME     NOT NULL,
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE statements (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT UNSIGNED NOT NULL,
  filename    VARCHAR(120) NOT NULL,
  period      VARCHAR(20)  NOT NULL,
  issued_at   DATETIME     NOT NULL,
  total_pence INT          NOT NULL DEFAULT 0,
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE favourites (
  customer_id INT UNSIGNED NOT NULL,
  product_id  INT UNSIGNED NOT NULL,
  created_at  DATETIME NOT NULL,
  PRIMARY KEY (customer_id, product_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE order_templates (
  id          INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  customer_id INT UNSIGNED NOT NULL,
  name        VARCHAR(120) NOT NULL,
  line_count  INT NOT NULL DEFAULT 0,
  updated_at  DATETIME NOT NULL,
  KEY customer_id (customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE literature (
  id           INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  filename     VARCHAR(120) NOT NULL,
  title        VARCHAR(160) NOT NULL,
  pages        INT NOT NULL DEFAULT 0,
  published_at DATE NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE news (
  id           INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  slug         VARCHAR(80)  NOT NULL,
  title        VARCHAR(200) NOT NULL,
  summary      VARCHAR(400) NOT NULL DEFAULT '',
  body         TEXT         NOT NULL,
  published_at DATE         NOT NULL,
  UNIQUE KEY slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE vacancies (
  id        INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  slug      VARCHAR(80)  NOT NULL,
  title     VARCHAR(160) NOT NULL,
  location  VARCHAR(80)  NOT NULL,
  body      TEXT         NOT NULL,
  closes_at DATE         NOT NULL,
  UNIQUE KEY slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE applications (
  id            INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_at    DATETIME NOT NULL,
  name          VARCHAR(120) NOT NULL DEFAULT '',
  email         VARCHAR(160) NOT NULL DEFAULT '',
  phone         VARCHAR(40)  NOT NULL DEFAULT '',
  vacancy_slug  VARCHAR(80)  NOT NULL DEFAULT '',
  covering_note TEXT         NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE enquiries (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL,
  name       VARCHAR(120) NOT NULL DEFAULT '',
  company    VARCHAR(160) NOT NULL DEFAULT '',
  email      VARCHAR(160) NOT NULL DEFAULT '',
  phone      VARCHAR(40)  NOT NULL DEFAULT '',
  message    TEXT         NOT NULL,
  kind       VARCHAR(20)  NOT NULL DEFAULT 'enquiry'
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE newsletter (
  email      VARCHAR(160) NOT NULL PRIMARY KEY,
  created_at DATETIME NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE feedback (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL,
  rating     TINYINT  NOT NULL DEFAULT 3,
  comment    TEXT     NOT NULL,
  depot      VARCHAR(60) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE stock_alerts (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL,
  email      VARCHAR(160) NOT NULL,
  reference  VARCHAR(40)  NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE password_resets (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  contact_id INT UNSIGNED NOT NULL,
  token      CHAR(32)     NOT NULL,
  created_at DATETIME     NOT NULL,
  KEY contact_id (contact_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- Every identifier this deployment has handed out. The morning report compares it with
-- what the depot terminals are using, which is how a terminal that has been re-imaged
-- with somebody else's profile gets noticed.
CREATE TABLE issued_sessions (
  sid        VARCHAR(128) NOT NULL PRIMARY KEY,
  created_at DATETIME     NOT NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE settings (
  name  VARCHAR(60)  NOT NULL PRIMARY KEY,
  value VARCHAR(200) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE audit_log (
  id         INT UNSIGNED NOT NULL AUTO_INCREMENT PRIMARY KEY,
  created_at DATETIME NOT NULL,
  actor      VARCHAR(120) NOT NULL DEFAULT '',
  action     VARCHAR(80)  NOT NULL DEFAULT '',
  detail     VARCHAR(400) NOT NULL DEFAULT ''
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
