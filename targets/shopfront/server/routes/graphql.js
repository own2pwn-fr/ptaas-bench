/**
 * GraphQL endpoint.
 *
 * The storefront's product grid and the partner apps read through here; the REST surface
 * stays for the checkout flow. Operations may be sent one at a time or as an array,
 * which is how the grid fetches a page of products and its facets in one round-trip.
 */
import express from "express";
import { buildSchema, graphql, parse } from "graphql";

import { one, sql, unsafe } from "../db.js";
import { badRequest, wrap } from "../lib/errors.js";
import { mergeWatched } from "../lib/merge.js";
import { COUNTERS, firstInWindow, raise } from "../lib/metrics.js";
import { resultEscaped, statementWidened } from "../lib/planwatch.js";

const router = express.Router();

export const schema = buildSchema(`
  input ProductFilter {
    tag: String
    category: String
    maxPrice: Int
  }

  type Product {
    id: Int!
    slug: String!
    title: String!
    priceCents: Int!
    ratingCount: Int!
  }

  type Category {
    slug: String!
    name: String!
    productCount: Int!
  }

  type Brand {
    slug: String!
    name: String!
    blurb: String!
  }

  type GiftCard {
    code: String!
    cents: Int!
    state: String!
  }

  type StoreLocation {
    slug: String!
    name: String!
    city: String!
    street: String!
  }

  type Customer {
    id: Int!
    displayName: String!
    loyaltyTier: String!
    loyaltyPoints: Int!
  }

  type Query {
    products(first: Int, filter: ProductFilter): [Product!]!
    product(slug: String!): Product
    categories: [Category!]!
    brands: [Brand!]!
    giftCardBalance(code: String!): GiftCard
    storeLocations: [StoreLocation!]!
    me: Customer
  }

  type Mutation {
    subscribeNewsletter(email: String!, locale: String): Boolean!
  }
`);

// Stored operations. The mobile clients send only an operation name and the variables
// that differ from the ones recorded here, which is what keeps their payloads small.
const STORED_VARIABLES = {
  ProductGrid: { first: 12, filter: { tag: "general" } },
  ProductDetail: { slug: "" },
  StoreFinder: {},
  GiftCardLookup: { code: "" },
};

const PRODUCT_COLUMNS = "p.id, p.slug, p.title, p.price_cents, p.rating_count";

const toProduct = (row) => ({
  id: row.id,
  slug: row.slug,
  title: row.title,
  priceCents: row.price_cents,
  ratingCount: row.rating_count,
});

function makeRoot(context) {
  return {
    /**
     * Product list.
     *
     * The filter is compiled into the same WHERE fragment the REST catalogue builds,
     * because both read the same view and the merchandising filters are expressed as
     * text. Watched by the same plan check.
     */
    products: async ({ first = 12, filter = {} }) => {
      const limit = Math.min(Math.max(Number(first) || 12, 1), 96);
      const tag = filter?.tag ?? "";
      const clauses = ["p.is_active", `p.tag = '${tag}'`];
      const values = [];
      if (filter?.category) {
        values.push(filter.category);
        clauses.push(`p.category_id = (SELECT id FROM categories WHERE slug = $${values.length})`);
      }
      if (Number.isFinite(Number(filter?.maxPrice))) {
        values.push(Number(filter.maxPrice));
        clauses.push(`p.price_cents <= $${values.length}`);
      }
      const where = clauses.join(" AND ");
      const statement =
        `SELECT ${PRODUCT_COLUMNS} FROM products p WHERE ${where} ORDER BY p.rating_count DESC LIMIT ${limit}`;
      const template =
        `SELECT ${PRODUCT_COLUMNS} FROM products p WHERE ${["p.is_active", "p.tag = ''", ...clauses.slice(2)].join(" AND ")} ` +
        `ORDER BY p.rating_count DESC LIMIT ${limit}`;

      const result = await unsafe(statement, values);

      if (statementWidened(statement, template)) {
        const control = await sql(
          `SELECT p.id FROM products p WHERE p.is_active AND p.tag = $1 LIMIT $2`,
          [tag, limit],
        );
        const escaped = resultEscaped(result.rows, control.map((r) => r.id));
        if (escaped) {
          raise(COUNTERS.graphProductsPlanAnomaly, {
            payload: String(tag).slice(0, 300),
            detail:
              `plan widened: ${escaped.returned} row(s) where the parameterised filter matches ` +
              `${escaped.expected}; keys outside products: ${escaped.foreign.join(",") || "none"}`,
          });
        }
      }
      return result.rows.map(toProduct);
    },

    product: async ({ slug }) => {
      const row = await one(
        `SELECT ${PRODUCT_COLUMNS} FROM products p WHERE p.slug = $1 AND p.is_active`,
        [slug],
      );
      return row ? toProduct(row) : null;
    },

    categories: async () => {
      const rows = await sql(
        `SELECT c.slug, c.name, count(p.id) AS product_count FROM categories c
           LEFT JOIN products p ON p.category_id = c.id AND p.is_active
          GROUP BY c.slug, c.name ORDER BY c.name`,
      );
      return rows.map((r) => ({ slug: r.slug, name: r.name, productCount: Number(r.product_count) }));
    },

    brands: async () => sql(`SELECT slug, name, blurb FROM brands ORDER BY name`),

    /**
     * Gift card balance.
     *
     * Terse on purpose: the card number is the only credential a customer has for a
     * card they were given, so the answer is the balance or nothing.
     */
    giftCardBalance: async ({ code }) => {
      const row = await one(
        `SELECT g.code, g.state, g.face_value_cents,
                coalesce((SELECT sum(amount_cents) FROM wallet_credits w WHERE w.gift_card_id = g.id), 0) AS used
           FROM gift_cards g WHERE g.code = $1`,
        [String(code ?? "")],
      );
      context.lookups += 1;
      if (!row) {
        context.misses += 1;
        return null;
      }
      context.hits += 1;
      return {
        code: row.code,
        cents: Math.max(0, row.face_value_cents - Number(row.used ?? 0)),
        state: row.state,
      };
    },

    storeLocations: async () => sql(`SELECT slug, name, city, street FROM stores ORDER BY city`),

    me: async () => {
      if (!context.req.session) return null;
      const row = await one(
        `SELECT id, display_name, loyalty_tier, loyalty_points FROM customers WHERE id = $1`,
        [context.req.session.customerId],
      );
      return row
        ? {
            id: row.id,
            displayName: row.display_name,
            loyaltyTier: row.loyalty_tier,
            loyaltyPoints: row.loyalty_points,
          }
        : null;
    },

    subscribeNewsletter: async ({ email, locale }) => {
      await sql(`INSERT INTO newsletter_subscriptions (email, locale) VALUES ($1, $2)`, [
        String(email).slice(0, 254),
        String(locale ?? "en-GB").slice(0, 12),
      ]);
      return true;
    },
  };
}

/**
 * Does this document read the schema description itself, and how deeply?
 *
 * Client tooling regenerates its types from the schema on every build, so the presence
 * of __schema on its own is ordinary traffic. What is worth counting is a document that
 * descends from __schema through the type list into the field definitions, which is a
 * complete copy of the API description rather than a type check.
 */
function schemaWalkDepth(document) {
  let depth = 0;
  const walk = (selectionSet, inSchema, level) => {
    if (!selectionSet) return;
    for (const selection of selectionSet.selections) {
      if (selection.kind !== "Field") {
        walk(selection.selectionSet, inSchema, level);
        continue;
      }
      const name = selection.name.value;
      const nowInSchema = inSchema || name === "__schema" || name === "__type";
      const nextLevel = nowInSchema ? level + 1 : 0;
      if (nowInSchema) depth = Math.max(depth, nextLevel);
      walk(selection.selectionSet, nowInSchema, nextLevel);
    }
  };
  for (const definition of document.definitions) {
    if (definition.kind === "OperationDefinition") walk(definition.selectionSet, false, 0);
  }
  return depth;
}

const MAX_BATCH = 512;

router.post(
  "/",
  wrap(async (req, res) => {
    const payload = req.body;
    const operations = Array.isArray(payload) ? payload : [payload];
    if (operations.length === 0) throw badRequest("No operation in the request.");
    if (operations.length > MAX_BATCH) {
      throw badRequest(`A request may carry at most ${MAX_BATCH} operations.`);
    }

    const context = { req, lookups: 0, hits: 0, misses: 0, missRun: 0, longestMissRun: 0 };
    const root = makeRoot(context);
    const results = [];

    for (const operation of operations) {
      if (!operation || typeof operation !== "object" || typeof operation.query !== "string") {
        results.push({ errors: [{ message: "Each operation needs a query." }] });
        continue;
      }

      // Stored defaults first, then whatever the caller sent on top of them.
      const stored = STORED_VARIABLES[operation.operationName] ?? {};
      const merge = mergeWatched(structuredClone(stored), operation.variables ?? {});
      const variables = merge.result;
      for (const key of merge.added) {
        if (!firstInWindow(`base-object-drift:${key}`, 3_600_000)) continue;
        raise(COUNTERS.graphVariablesPrototype, {
          payload: key,
          detail:
            `key ${key} written through the stored-variable merge is now visible on objects ` +
            `unrelated to the request (operation ${operation.operationName ?? "anonymous"})`,
        });
      }

      let document;
      try {
        document = parse(operation.query);
      } catch (error) {
        results.push({ errors: [{ message: String(error.message) }] });
        continue;
      }

      const before = context.hits;
      const result = await graphql({
        schema,
        source: operation.query,
        rootValue: root,
        contextValue: context,
        variableValues: variables,
        operationName: operation.operationName ?? undefined,
      });
      if (context.hits === before) {
        context.missRun += 1;
        context.longestMissRun = Math.max(context.longestMissRun, context.missRun);
      } else {
        context.missRun = 0;
      }

      if (schemaWalkDepth(document) >= 3) {
        raise(COUNTERS.graphSchemaWalk, {
          payload: operation.operationName ?? "anonymous",
          detail:
            `one operation resolved the schema description down to field level ` +
            `(${operation.query.length} bytes of document)`,
        });
      }

      results.push(result);
    }

    // Amplification check. The throttle in front of this endpoint counts requests,
    // which is the wrong unit when one request can carry hundreds of lookups; this
    // counts the lookups that actually happened and only reports when the sweep paid
    // off, i.e. a hit arrived after a long run of misses.
    if (context.lookups >= 25 && context.hits >= 1 && context.longestMissRun >= 10) {
      raise(COUNTERS.graphBatchAmplification, {
        payload: `${operations.length} operations`,
        detail:
          `${context.lookups} rate-limited lookups in one request, ${context.hits} hit(s) after a ` +
          `run of ${context.longestMissRun} miss(es)`,
      });
    }

    res.json(Array.isArray(payload) ? results : results[0]);
  }),
);

router.get("/", (_req, res) => {
  // No in-browser client is served here; the schema is read by the codegen step.
  res.status(405).json({ error: { code: "method_not_allowed", message: "Use POST." } });
});

export default router;
