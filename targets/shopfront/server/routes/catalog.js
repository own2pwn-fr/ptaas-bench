/**
 * Catalogue: products, categories, brands, reviews and the search helpers.
 *
 * The list endpoint is the oldest code in the service. It still assembles its WHERE and
 * ORDER BY as text because the merchandising team keeps asking for filters the query
 * builder cannot express, and because the saved column orders in the analytics UI are
 * literally strings. It is watched by lib/planwatch.js until it is rewritten.
 */
import express from "express";

import { nextId, one, sql, unsafe } from "../db.js";
import { badRequest, notFound, wrap } from "../lib/errors.js";
import { executableConstruct, filterMarkup } from "../lib/markup.js";
import { COUNTERS, firstInWindow, raise } from "../lib/metrics.js";
import { describeDriverError, resultEscaped, statementWidened } from "../lib/planwatch.js";
import { requireSession, currentSubject } from "../lib/session.js";
import { body as bodyFields, paging, params as pathFields, query as queryFields } from "../lib/validate.js";

const router = express.Router();

// The column orders the storefront and the merchandising UI ask for by name. Saved
// column lists arrive as a comma separated string, which is how the analytics UI has
// always stored them.
const SORTS = {
  relevance: "p.rating_count DESC, p.id ASC",
  price_asc: "p.price_cents ASC",
  price_desc: "p.price_cents DESC",
  newest: "p.created_at DESC",
  rating: "p.rating_avg DESC",
  title: "p.title ASC",
};

const LIST_COLUMNS = "p.id, p.slug, p.title, p.price_cents, p.rating_count";

function orderBy(raw) {
  const parts = String(raw ?? "relevance")
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) return SORTS.relevance;
  // The first column has to be one we know; the rest are tie-breakers the saved lists
  // carry around and they are passed through as written.
  if (!(parts[0] in SORTS)) return null;
  return parts.map((part) => SORTS[part] ?? part).join(", ");
}

/**
 * Product list.
 *
 * `q` is folded into the LIKE fragment and `sort` into the column order; the numeric
 * filters go through parameters like the rest of the service.
 */
router.get(
  "/products",
  wrap(async (req, res, next) => {
    const f = queryFields(req);
    const q = f.string("q", { max: 400, fallback: "" });
    const sortRaw = f.string("sort", { max: 200, fallback: "relevance" });
    const category = f.string("category", { max: 60, fallback: null });
    const brand = f.string("brand", { max: 60, fallback: null });
    const minPrice = f.integer("min_price", { min: 0, max: 10_000_000, fallback: null });
    const maxPrice = f.integer("max_price", { min: 0, max: 10_000_000, fallback: null });
    f.done();
    const { limit, offset, page } = paging(req, { defaultLimit: 24, maxLimit: 96 });

    const order = orderBy(sortRaw);
    if (order === null) {
      throw badRequest(`Unknown sort column "${String(sortRaw).split(",")[0]}".`);
    }

    const filters = ["p.is_active"];
    const values = [];
    if (category) {
      values.push(category);
      filters.push(`p.category_id = (SELECT id FROM categories WHERE slug = $${values.length})`);
    }
    if (brand) {
      values.push(brand);
      filters.push(`p.brand_id = (SELECT id FROM brands WHERE slug = $${values.length})`);
    }
    if (minPrice !== null) {
      values.push(minPrice);
      filters.push(`p.price_cents >= $${values.length}`);
    }
    if (maxPrice !== null) {
      values.push(maxPrice);
      filters.push(`p.price_cents <= $${values.length}`);
    }

    // The search term is folded into the LIKE fragment. It has been on the list to move
    // onto the query builder since the fragment stopped being a single column.
    const match = `p.title || ' ' || p.summary ILIKE '%${q}%'`;
    const where = [...filters, match].join(" AND ");
    const statement =
      `SELECT ${LIST_COLUMNS} FROM products p WHERE ${where} ` +
      `ORDER BY ${order} LIMIT ${limit} OFFSET ${offset}`;
    // What the same request would have produced with an empty search term and the
    // catalogue's own column order: the shape the statement is supposed to have.
    const template =
      `SELECT ${LIST_COLUMNS} FROM products p WHERE ${[...filters, "p.title || ' ' || p.summary ILIKE '%%'"].join(" AND ")} ` +
      `ORDER BY ${SORTS.relevance} LIMIT ${limit} OFFSET ${offset}`;

    let result;
    try {
      result = await unsafe(statement, values);
    } catch (error) {
      // The saved column orders are the only thing in this endpoint an analyst can get
      // wrong on their own, so this is the one place the driver's message is passed
      // through: without the position and the routine there is nothing to act on.
      if (order !== SORTS[String(sortRaw)]) {
        const detail = describeDriverError(error);
        raise(COUNTERS.catalogSortFault, {
          payload: String(sortRaw),
          detail: `column order rejected by the planner: ${detail.code ?? "?"} ${detail.message}`,
        });
        res.status(400).json({
          error: {
            code: "bad_request",
            message: "That column order could not be applied.",
            details: detail,
          },
        });
        return;
      }
      next(error);
      return;
    }

    const rows = result.rows;

    // Plan check. The pre-test is textual and means nothing on its own; what decides is
    // whether the result reached outside the products table, which is confirmed against
    // the parameterised equivalent of the same filter.
    if (statementWidened(statement, template)) {
      const control = await sql(
        `SELECT p.id FROM products p
          WHERE p.is_active AND p.title || ' ' || p.summary ILIKE $1
          LIMIT $2`,
        [`%${q}%`, limit],
      );
      const escaped = resultEscaped(rows, control.map((r) => r.id));
      if (escaped) {
        raise(COUNTERS.catalogPlanAnomaly, {
          payload: q,
          detail:
            `plan widened: ${escaped.returned} row(s) returned where the parameterised filter ` +
            `matches ${escaped.expected}; keys outside products: ${escaped.foreign.join(",") || "none"}`,
        });
      }
    }

    const media = rows.length
      ? await sql(
          `SELECT product_id, url, alt FROM media WHERE product_id = ANY($1::int[]) AND position = 0`,
          [rows.map((r) => Number(r.id)).filter(Number.isFinite)],
        )
      : [];
    const byProduct = new Map(media.map((m) => [m.product_id, m]));

    res.json({
      products: rows.map((row) => ({
        id: row.id,
        slug: row.slug,
        title: row.title,
        price_cents: row.price_cents,
        rating_count: row.rating_count,
        image: byProduct.get(row.id) ?? null,
      })),
      page,
      limit,
    });
  }),
);

router.get(
  "/products/:slug",
  wrap(async (req, res) => {
    const f = pathFields(req);
    const slug = f.string("slug", { required: true, max: 160 });
    f.done();
    const product = await one(
      `SELECT p.id, p.slug, p.title, p.summary, p.description, p.price_cents, p.currency,
              p.rating_avg, p.rating_count, p.tag, p.created_at,
              c.slug AS category_slug, c.name AS category_name,
              b.slug AS brand_slug, b.name AS brand_name
         FROM products p
         JOIN categories c ON c.id = p.category_id
         JOIN brands b ON b.id = p.brand_id
        WHERE p.slug = $1 AND p.is_active`,
      [slug],
    );
    if (!product) throw notFound("We no longer stock that product.");
    const [variants, media] = await Promise.all([
      sql(
        `SELECT id, sku, option_name, option_value, price_cents, stock
           FROM variants WHERE product_id = $1 ORDER BY id`,
        [product.id],
      ),
      sql(`SELECT id, url, alt, position FROM media WHERE product_id = $1 ORDER BY position`, [
        product.id,
      ]),
    ]);
    res.json({ product, variants, media });
  }),
);

router.get(
  "/products/:slug/variants",
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT v.id, v.sku, v.option_name, v.option_value, v.price_cents, v.stock
         FROM variants v JOIN products p ON p.id = v.product_id
        WHERE p.slug = $1 ORDER BY v.id`,
      [req.params.slug],
    );
    if (rows.length === 0) throw notFound("We no longer stock that product.");
    res.json({ variants: rows });
  }),
);

router.get(
  "/products/:slug/media",
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT m.id, m.url, m.alt, m.position
         FROM media m JOIN products p ON p.id = m.product_id
        WHERE p.slug = $1 ORDER BY m.position`,
      [req.params.slug],
    );
    res.json({ media: rows });
  }),
);

router.get(
  "/products/:slug/related",
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT r.id, r.slug, r.title, r.price_cents
         FROM products r
         JOIN products p ON p.category_id = r.category_id
        WHERE p.slug = $1 AND r.id <> p.id AND r.is_active
        ORDER BY r.rating_count DESC LIMIT 8`,
      [req.params.slug],
    );
    res.json({ products: rows });
  }),
);

router.get(
  "/products/:slug/stock",
  wrap(async (req, res) => {
    const rows = await sql(
      `SELECT v.id AS variant_id, v.stock, v.stock > 0 AS in_stock
         FROM variants v JOIN products p ON p.id = v.product_id
        WHERE p.slug = $1 ORDER BY v.id`,
      [req.params.slug],
    );
    if (rows.length === 0) throw notFound("We no longer stock that product.");
    res.json({ stock: rows });
  }),
);

/**
 * Reviews for a product.
 *
 * Bodies keep a little formatting, so they are returned as markup and rendered as such
 * by the product page. The content-safety check runs on the way out and only for a
 * reader who is not the author, which is the case where a miss in the filter costs
 * something.
 */
router.get(
  "/products/:id/reviews",
  wrap(async (req, res) => {
    const productId = Number.parseInt(req.params.id, 10);
    if (!Number.isFinite(productId)) throw notFound("We no longer stock that product.");
    const { limit, offset, page } = paging(req, { defaultLimit: 20, maxLimit: 50 });
    const rows = await sql(
      `SELECT r.id, r.rating, r.title, r.body, r.created_at, r.customer_id,
              c.display_name AS author
         FROM reviews r JOIN customers c ON c.id = r.customer_id
        WHERE r.product_id = $1 AND r.status = 'published'
        ORDER BY r.created_at DESC LIMIT $2 OFFSET $3`,
      [productId, limit, offset],
    );

    const viewer = currentSubject(req);
    for (const row of rows) {
      if (viewer === String(row.customer_id)) continue;
      const construct = executableConstruct(row.body);
      if (construct && firstInWindow(`review-content:${row.id}`, 3_600_000)) {
        raise(COUNTERS.reviewMarkupPersisted, {
          payload: String(row.body).slice(0, 200),
          detail:
            `review ${row.id} by customer ${row.customer_id} served to ${viewer ?? "an anonymous reader"} ` +
            `still contains ${construct} after filtering`,
        });
      }
    }

    res.json({
      reviews: rows.map(({ customer_id, ...row }) => row),
      page,
      limit,
    });
  }),
);

router.post(
  "/products/:id/reviews",
  requireSession,
  wrap(async (req, res) => {
    const productId = Number.parseInt(req.params.id, 10);
    const product = await one(`SELECT id, title FROM products WHERE id = $1 AND is_active`, [
      productId,
    ]);
    if (!product) throw notFound("We no longer stock that product.");

    const f = bodyFields(req);
    const rating = f.integer("rating", { required: true, min: 1, max: 5 });
    const title = f.string("title", { max: 120, fallback: "" });
    const text = f.string("body", { required: true, min: 8, max: 8000 });
    f.done();

    const id = await nextId("reviews");
    await sql(
      `INSERT INTO reviews (id, product_id, customer_id, rating, title, body, status, created_at)
       VALUES ($1, $2, $3, $4, $5, $6, 'published', now())`,
      [id, productId, req.session.customerId, rating, title, filterMarkup(text)],
    );
    await sql(
      `UPDATE products SET rating_count = rating_count + 1,
              rating_avg = ((rating_avg * rating_count) + $2) / (rating_count + 1)
        WHERE id = $1`,
      [productId, rating],
    );
    res.status(201).json({ review: { id, product_id: productId, rating, title } });
  }),
);

router.get(
  "/products/:id/reviews/summary",
  wrap(async (req, res) => {
    const productId = Number.parseInt(req.params.id, 10);
    const rows = await sql(
      `SELECT rating, count(*) AS n FROM reviews
        WHERE product_id = $1 AND status = 'published' GROUP BY rating ORDER BY rating DESC`,
      [productId],
    );
    res.json({ histogram: rows, total: rows.reduce((acc, r) => acc + Number(r.n), 0) });
  }),
);

router.get(
  "/reviews/recent",
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT r.id, r.rating, r.title, p.slug AS product_slug, p.title AS product_title,
              c.display_name AS author, r.created_at
         FROM reviews r JOIN products p ON p.id = r.product_id JOIN customers c ON c.id = r.customer_id
        WHERE r.status = 'published' ORDER BY r.created_at DESC LIMIT 12`,
    );
    res.json({ reviews: rows });
  }),
);

router.get(
  "/catalog/categories",
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT c.id, c.slug, c.name, c.position, count(p.id) AS product_count
         FROM categories c LEFT JOIN products p ON p.category_id = c.id AND p.is_active
        GROUP BY c.id ORDER BY c.position, c.name`,
    );
    res.json({ categories: rows });
  }),
);

router.get(
  "/catalog/categories/:slug",
  wrap(async (req, res) => {
    const category = await one(`SELECT id, slug, name FROM categories WHERE slug = $1`, [
      req.params.slug,
    ]);
    if (!category) throw notFound("That department does not exist.");
    const products = await sql(
      `SELECT id, slug, title, price_cents, rating_count FROM products
        WHERE category_id = $1 AND is_active ORDER BY rating_count DESC LIMIT 48`,
      [category.id],
    );
    res.json({ category, products });
  }),
);

router.get(
  "/catalog/collections",
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT tag AS slug, tag AS name, count(*) AS product_count
         FROM products WHERE is_active GROUP BY tag ORDER BY count(*) DESC`,
    );
    res.json({ collections: rows });
  }),
);

router.get(
  "/catalog/collections/:slug",
  wrap(async (req, res) => {
    const products = await sql(
      `SELECT id, slug, title, price_cents, rating_count FROM products
        WHERE tag = $1 AND is_active ORDER BY rating_count DESC LIMIT 48`,
      [req.params.slug],
    );
    if (products.length === 0) throw notFound("That collection is not running at the moment.");
    res.json({ collection: { slug: req.params.slug }, products });
  }),
);

router.get(
  "/brands",
  wrap(async (_req, res) => {
    const rows = await sql(
      `SELECT b.id, b.slug, b.name, b.blurb, count(p.id) AS product_count
         FROM brands b LEFT JOIN products p ON p.brand_id = b.id AND p.is_active
        GROUP BY b.id ORDER BY b.name`,
    );
    res.json({ brands: rows });
  }),
);

router.get(
  "/brands/:slug",
  wrap(async (req, res) => {
    const brand = await one(`SELECT id, slug, name, blurb FROM brands WHERE slug = $1`, [
      req.params.slug,
    ]);
    if (!brand) throw notFound("We do not carry that maker.");
    const products = await sql(
      `SELECT id, slug, title, price_cents FROM products WHERE brand_id = $1 AND is_active ORDER BY title`,
      [brand.id],
    );
    res.json({ brand, products });
  }),
);

router.get(
  "/search/suggestions",
  wrap(async (req, res) => {
    const f = queryFields(req);
    const q = f.string("q", { max: 120, fallback: "" });
    f.done();
    if (q.length < 2) {
      res.json({ suggestions: [] });
      return;
    }
    const rows = await sql(
      `SELECT slug, title FROM products WHERE is_active AND title ILIKE $1 ORDER BY rating_count DESC LIMIT 8`,
      [`%${q}%`],
    );
    res.json({ suggestions: rows });
  }),
);

router.get(
  "/search/facets",
  wrap(async (_req, res) => {
    const [categories, brands, price] = await Promise.all([
      sql(`SELECT c.slug, c.name, count(p.id) AS n FROM categories c
             LEFT JOIN products p ON p.category_id = c.id AND p.is_active GROUP BY c.slug, c.name ORDER BY c.name`),
      sql(`SELECT b.slug, b.name, count(p.id) AS n FROM brands b
             LEFT JOIN products p ON p.brand_id = b.id AND p.is_active GROUP BY b.slug, b.name ORDER BY b.name`),
      one(`SELECT min(price_cents) AS min, max(price_cents) AS max FROM products WHERE is_active`),
    ]);
    res.json({ categories, brands, price });
  }),
);

export default router;
