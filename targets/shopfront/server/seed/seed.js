/**
 * Deterministic storefront dataset.
 *
 * The whole catalogue, customer base and order history is rebuilt from `schema.sql` plus
 * the rows this module composes. Two resets of the same estate have to produce byte
 * identical state, so nothing here reads the clock or the system entropy pool:
 *
 *   - content (names, copy, credentials) comes out of `deriveIdentity(config.deploySeed)`,
 *   - every timestamp is `EPOCH` plus a fixed offset,
 *   - every pseudo random choice comes from a named stream of `rngFrom(seed + ":topic")`,
 *     so adding a stream later does not shift the rows an existing stream produced.
 *
 * Numeric identifiers are pinned: support engineers quote order 1001 and ticket
 * 7001 in write-ups, and those have to mean the same row on every estate even though the
 * names and the copy differ.
 */
import { createHash } from "node:crypto";
import { readFile } from "node:fs/promises";

import config from "../config.js";
import { hashPassword } from "../lib/auth.js";
import { deriveIdentity, rngFrom } from "../lib/identity.js";

/* -------------------------------------------------------------------------- */
/* Fixed points                                                                */
/* -------------------------------------------------------------------------- */

/** Every seeded timestamp is this instant plus a constant offset. */
const EPOCH = Date.parse("2026-01-06T09:00:00Z");

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const STAFF_ID = 1;
const PRIMARY_ID = 1001;
const SECONDARY_ID = 1002;
const CUSTOMER_FIRST_ID = 1001;
const CUSTOMER_LAST_ID = 1120;
const CUSTOMER_COUNT = CUSTOMER_LAST_ID - CUSTOMER_FIRST_ID + 1; // 120
const ACCOUNT_COUNT = CUSTOMER_COUNT + 1; // plus the operations account, id 1

const CATEGORY_FIRST_ID = 10;
const BRAND_FIRST_ID = 20;
const PRODUCT_FIRST_ID = 2001;
const PRODUCT_COUNT = 96;
const VARIANT_FIRST_ID = 3101;
const CART_FIRST_ID = 4001;
const CART_COUNTER_START = 5000;
const CHECKOUT_FIRST_ID = 5001;
const CHECKOUT_COUNTER_START = 6000;
const CART_ITEM_FIRST_ID = 9001;
const ORDER_FIRST_ID = 1001;
const ORDER_COUNT = 60;
const TICKET_FIRST_ID = 7001;
const TICKET_COUNT = 60;
const GIFT_CARD_COUNT = 40;
const IMPORT_COUNTER_START = 100;

/** Tables this module writes, in the order they have to be inserted. */
export const SEED_TABLES = [
  "categories",
  "brands",
  "customers",
  "addresses",
  "payment_methods",
  "account_preferences",
  "products",
  "variants",
  "media",
  "reviews",
  "carts",
  "cart_items",
  "coupons",
  "checkout_sessions",
  "checkout_coupons",
  "orders",
  "order_items",
  "order_transitions",
  "shipments",
  "order_returns",
  "coupon_redemptions",
  "support_tickets",
  "support_messages",
  "support_articles",
  "gift_cards",
  "wallet_credits",
  "wishlists",
  "wishlist_items",
  "saved_searches",
  "notifications",
  "loyalty_transactions",
  "imports",
  "stores",
  "store_hours",
  "content_pages",
  "banners",
  "id_counters",
];

/**
 * Column projection used by the digest.
 *
 * Written out column by column rather than `SELECT *` so that adding a column to the
 * schema is a visible change here instead of a silent change of digest.
 */
const DIGEST_PROJECTION = {
  categories: ["id", "slug", "name", "position"],
  brands: ["id", "slug", "name", "blurb"],
  customers: [
    "id", "email", "password_hash", "password_salt", "given_name", "family_name",
    "display_name", "phone", "role", "loyalty_tier", "loyalty_points", "avatar_url",
    "marketing_opt_in", "created_at",
  ],
  addresses: [
    "id", "customer_id", "label", "recipient", "line1", "line2", "city", "postcode",
    "country", "is_default",
  ],
  payment_methods: ["id", "customer_id", "brand", "last4", "exp_month", "exp_year", "is_default"],
  account_preferences: ["customer_id", "locale", "currency", "theme", "widgets", "updated_at"],
  products: [
    "id", "slug", "title", "summary", "description", "category_id", "brand_id",
    "price_cents", "currency", "rating_avg", "rating_count", "tag", "is_active", "created_at",
  ],
  variants: ["id", "product_id", "sku", "option_name", "option_value", "price_cents", "stock"],
  media: ["id", "product_id", "url", "alt", "position"],
  reviews: ["id", "product_id", "customer_id", "rating", "title", "body", "status", "created_at"],
  carts: ["id", "token", "customer_id", "currency", "meta", "created_at", "updated_at"],
  cart_items: ["id", "cart_id", "variant_id", "quantity", "unit_price_cents", "added_at"],
  coupons: [
    "id", "code", "description", "percent_off", "amount_off_cents", "max_redemptions",
    "redemptions", "is_active", "expires_at", "created_by", "created_at",
  ],
  checkout_sessions: [
    "id", "cart_id", "customer_id", "address_id", "payment_method_id", "shipping_method",
    "shipping_rate_cents", "state", "created_at",
  ],
  checkout_coupons: ["id", "session_id", "code", "applied_at"],
  orders: [
    "id", "reference", "customer_id", "address_id", "state", "currency", "subtotal_cents",
    "shipping_cents", "discount_cents", "total_cents", "placed_at",
  ],
  order_items: ["id", "order_id", "variant_id", "title", "quantity", "unit_price_cents", "line_total_cents"],
  order_transitions: ["id", "order_id", "from_state", "to_state", "actor_subject", "actor_role", "created_at"],
  shipments: ["id", "order_id", "carrier", "tracking_ref", "state", "shipped_at"],
  order_returns: ["id", "order_id", "reason", "state", "created_at"],
  coupon_redemptions: ["id", "coupon_id", "order_id", "customer_id", "code", "redeemed_at"],
  support_tickets: ["id", "reference", "customer_id", "subject", "status", "priority", "created_at"],
  support_messages: ["id", "ticket_id", "author_kind", "author_subject", "body", "created_at"],
  support_articles: ["id", "slug", "title", "category", "body"],
  gift_cards: ["id", "code", "face_value_cents", "customer_id", "state", "issued_at"],
  wallet_credits: ["id", "customer_id", "gift_card_id", "amount_cents", "memo", "created_at"],
  wishlists: ["id", "customer_id", "name", "created_at"],
  wishlist_items: ["id", "wishlist_id", "variant_id", "added_at"],
  saved_searches: ["id", "customer_id", "label", "rule", "created_at"],
  notifications: ["id", "customer_id", "kind", "body", "read_at", "created_at"],
  loyalty_transactions: ["id", "customer_id", "points", "reason", "created_at"],
  imports: ["id", "source_url", "state", "requested_by", "rows_seen", "created_at"],
  stores: ["id", "slug", "name", "city", "street", "phone"],
  store_hours: ["id", "store_id", "weekday", "opens", "closes"],
  content_pages: ["id", "slug", "title", "body", "updated_at"],
  banners: ["id", "slug", "headline", "body", "cta_url", "position"],
  id_counters: ["name", "value"],
};

/** Sort key that makes the digest independent of physical row order. */
const DIGEST_ORDER = {
  account_preferences: "customer_id",
  id_counters: "name",
};

/* -------------------------------------------------------------------------- */
/* Small deterministic helpers                                                 */
/* -------------------------------------------------------------------------- */

const ts = (offsetMs) => new Date(EPOCH + offsetMs).toISOString();
const pickFrom = (rnd, list) => list[Math.floor(rnd() * list.length) % list.length];
const intBetween = (rnd, lo, hi) => lo + (Math.floor(rnd() * (hi - lo + 1)) % (hi - lo + 1));
const pad = (value, width) => String(value).padStart(width, "0");
const slugify = (value) => value.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

/** Stable per-account salt, so a reset rewrites the same hash bytes. */
const saltFor = (seed, subject) =>
  createHash("sha256").update(`${seed}:password-salt:${subject}`).digest("hex").slice(0, 32);

/* -------------------------------------------------------------------------- */
/* Word banks                                                                  */
/* -------------------------------------------------------------------------- */

const PASSPHRASE_WORDS = [
  "Harbour", "Quarry", "Lantern", "Meadow", "Copper", "Thistle", "Willow", "Pebble",
  "Granite", "Clover", "Juniper", "Cinder", "Bracken", "Saffron", "Linden", "Marram",
  "Orchard", "Rowan", "Sable", "Tamarisk", "Kestrel", "Bramble", "Fennel", "Cobble",
];
const PASSPHRASE_SYMBOLS = ["!", "%", "*", "+", "="];

// Used only when the derived roster is shorter than the account block, which happens
// when two generated people share a mailbox local part.
const SPARE_GIVEN = [
  "Elena", "Bruno", "Saskia", "Matteo", "Freya", "Idris", "Camille", "Otto",
  "Leonie", "Hugo", "Aurora", "Viktor", "Delphine", "Mateus", "Sinead", "Kai",
];
const SPARE_FAMILY = [
  "Ashworth", "Baptiste", "Carvalho", "Doorn", "Engberg", "Farrell", "Grimaldi",
  "Hjelm", "Ingram", "Janowski", "Karlsen", "Lombardi", "Mensah", "Nystrom",
  "Oduya", "Pettersen",
];

const CATEGORIES = [
  { slug: "kitchen", name: "Kitchen & Table" },
  { slug: "outdoor", name: "Outdoor & Camp" },
  { slug: "travel", name: "Travel & Carry" },
  { slug: "home", name: "Home & Living" },
  { slug: "garden", name: "Garden & Allotment" },
  { slug: "workshop", name: "Workshop & Tools" },
  { slug: "pets", name: "Pets" },
  { slug: "cycling", name: "Cycling" },
];

/** First keyword that matches decides the shelf a product sits on. */
const SHELF_RULES = [
  [/kettle|mug|bowl|chopping|apron|flask|kneader/i, "kitchen"],
  [/camp|lantern|sleeping|tarpaulin|deck chair|picnic|storm cap|field jacket/i, "outdoor"],
  [/rucksack|tote|wash bag|wall hook/i, "travel"],
  [/blanket|draught|log carrier|serving/i, "home"],
  [/watering|trug|plant|bird feeder|kneeler/i, "garden"],
  [/tool roll|hook/i, "workshop"],
  [/dog|lead/i, "pets"],
  [/bike|pannier/i, "cycling"],
];

const BRAND_NAMES = [
  "Ashgrove", "Fettlework", "Halloway", "Pike & Fen", "Ormside", "Tarnbrook",
  "Wold & Wick", "Caldbeck", "Ravensmoor", "Stourbank", "Hartwell", "Nethercote",
  "Cranmere", "Selby Row", "Brambleton", "Ferrous & Fold",
];
const BRAND_BLURBS = [
  "A small workshop that has been turning out the same short list of pieces since the eighties.",
  "Family run, stubbornly slow, and unwilling to change a pattern that already works.",
  "Known for repairable joinery and a spares list that goes back fifteen years.",
  "Cuts and stitches everything in one building, which is why the sizing never drifts.",
  "Works in materials that age visibly, so a piece looks lived in rather than worn out.",
  "Built its name on hardware that survives a winter outdoors without complaint.",
  "Prices are steady because the tooling is paid off and the patterns rarely change.",
  "Sends a repair kit with anything that has a seam, a strap or a moving part.",
];

const PLACES = [
  { city: "Bristol", country: "United Kingdom", style: "gb", dial: "+44" },
  { city: "Leeds", country: "United Kingdom", style: "gb", dial: "+44" },
  { city: "Ghent", country: "Belgium", style: "num4", dial: "+32" },
  { city: "Utrecht", country: "Netherlands", style: "nl", dial: "+31" },
  { city: "Aarhus", country: "Denmark", style: "num4", dial: "+45" },
  { city: "Porto", country: "Portugal", style: "pt", dial: "+351" },
  { city: "Girona", country: "Spain", style: "num5", dial: "+34" },
  { city: "Trieste", country: "Italy", style: "num5", dial: "+39" },
  { city: "Malmo", country: "Sweden", style: "num5", dial: "+46" },
  { city: "Cork", country: "Ireland", style: "ie", dial: "+353" },
  { city: "Nantes", country: "France", style: "num5", dial: "+33" },
  { city: "Bergen", country: "Norway", style: "num4", dial: "+47" },
];
const STREETS = [
  "Wharf Road", "Kiln Lane", "Sowerby Street", "Millrace Walk", "Pentland Row",
  "Cobbold Street", "Hazelbank Road", "Tannery Yard", "Fold Street", "Ropewalk",
  "Northgate Crescent", "Quayside Terrace", "Saltbox Lane", "Weavers Green",
];
const ADDRESS_LABELS = ["Home", "Work", "Parents", "Weekend place", "Studio"];
const ADDRESS_LINE2 = [null, null, null, "Flat 2", "Unit 4B", "Second floor", "Rear entrance"];
const POSTCODE_LETTERS = "ABDEFGHJLNPQRSTUWXYZ";

const CARD_BRANDS = ["Visa", "Mastercard", "Maestro", "American Express"];

const OPTION_SETS = [
  { name: "Size", values: ["Small", "Medium", "Large"] },
  { name: "Colour", values: ["Moss", "Slate", "Ochre"] },
  { name: "Capacity", values: ["0.5 L", "0.75 L", "1 L"] },
  { name: "Finish", values: ["Natural", "Waxed", "Blackened"] },
  { name: "Length", values: ["Short", "Regular", "Long"] },
];

const PRODUCT_SUMMARIES = [
  "Made in small batches and finished by hand, so no two are quite alike.",
  "A plain, hard-wearing piece meant to be used every week rather than admired.",
  "Cut from stock that ages well and stitched to be repaired rather than replaced.",
  "Weighted and balanced for long days, with nothing on it that can snag.",
  "Simple hardware, honest materials, and a shape that has not changed in years.",
  "Designed around one job, then trimmed of everything that did not help.",
];
const DESC_OPEN = [
  "The {title} started as a request from customers who kept wearing out cheaper versions.",
  "We drew up the {title} around a single job and refused to bolt anything else on.",
  "Every {title} leaves the workshop with the same short list of parts it had a decade ago.",
  "The {title} is the piece our own staff reach for when they are packing at short notice.",
  "Our makers keep the {title} plain on purpose, because the plain ones come back least often.",
];
const DESC_BODY = [
  "The seams are lockstitched and the stress points are riveted, so the shape holds after a wet winter.",
  "Fittings are solid rather than plated, which means a scuff polishes out instead of flaking away.",
  "It packs down small, wipes clean with warm water, and dries without holding a smell.",
  "The finish darkens with handling, and a coat of wax once a year keeps it weather-ready.",
  "Nothing is glued shut: straps, liners and fastenings can all be swapped from our spares list.",
  "It is heavier than the lightest thing on the shelf, and that weight is where the durability lives.",
];
const DESC_CLOSE = [
  "Covered by our five-year repair promise, spares included.",
  "Ships flat-packed in recycled board with no plastic inside.",
  "Comes with a short card of care notes written by the people who made it.",
  "Sized to fit the rest of the range, so lids, straps and liners interchange.",
  "Stocked year round, restocked every six weeks.",
];
const PRODUCT_TAGS = ["general", "core", "new-in", "seasonal", "clearance"];

const MEDIA_VIEWS = [
  "photographed from the front on a pale background",
  "shown in use on a kitchen worktop",
  "with the strap extended to full length",
  "packed for a weekend away",
  "detail of the stitching and hardware",
  "beside the rest of the range for scale",
];

const RATING_POOL = [5, 5, 5, 5, 5, 4, 4, 4, 4, 3, 3, 2, 1];
const REVIEW_TITLES_GOOD = [
  "Exactly what I hoped for", "Well made and practical", "Worth the money",
  "A solid everyday piece", "Better than the one it replaced", "Handsome and hard-wearing",
  "Has earned its place", "Second one I have bought",
];
const REVIEW_TITLES_MIXED = [
  "Good, with one reservation", "Does the job, runs small", "Nearly right for me",
  "Fine once I got used to it", "Decent, if a little heavy",
];
const REVIEW_TITLES_POOR = [
  "Not right for what I needed", "Arrived marked", "Good idea, average finish",
  "Sent it back in the end",
];
const REVIEW_OPEN_GOOD = [
  "I have had the {title} for a couple of months now and it has been faultless.",
  "Bought the {title} on a recommendation and I am glad I did.",
  "The {title} arrived quickly and felt substantial straight out of the box.",
  "This is the third piece I have bought from the range and the {title} is the pick of them.",
];
const REVIEW_OPEN_MIXED = [
  "The {title} is well put together, though it is not quite what I pictured.",
  "I like the {title}, but it took a while to get on with.",
  "Quality is there on the {title}; the sizing caught me out.",
];
const REVIEW_OPEN_POOR = [
  "The {title} did not work out for me.",
  "Sadly the {title} was not what I needed.",
  "I wanted to like the {title} more than I did.",
];
const REVIEW_MIDDLE = [
  "The stitching has held up to weekly use and there is no sign of stretch at the corners.",
  "It cleans up with a damp cloth and has not picked up any smell.",
  "The hardware still moves smoothly after a winter of being left outside.",
  "It is heavier than I expected, which turned out to be a good thing.",
  "The finish has darkened slightly, which I think suits it.",
  "Packing was plain card with no plastic, which I appreciated.",
];
const REVIEW_CLOSE_GOOD = [
  "I would buy another without hesitating.",
  "Happy to recommend it to anyone on the fence.",
  "It has already paid for itself in things I have not had to replace.",
];
const REVIEW_CLOSE_MIXED = [
  "Worth checking the measurements before you order.",
  "Good value if you know what you are getting.",
  "The support team answered my questions quickly, which helped.",
];

const COUPON_ROWS = [
  {
    code: "SPRING-15", description: "Fifteen per cent off the spring range.",
    percent_off: 15, amount_off_cents: null, max_redemptions: 500, is_active: true,
    expires_offset: 210 * DAY,
  },
  {
    code: "WELCOME-ONCE", description: "Ten euro off a first order, one use in total.",
    percent_off: null, amount_off_cents: 1000, max_redemptions: 1, is_active: true,
    expires_offset: null, fixed_redemptions: 0,
  },
  {
    code: "SUMMER-10", description: "Ten per cent off outdoor and camp.",
    percent_off: 10, amount_off_cents: null, max_redemptions: 300, is_active: true,
    expires_offset: 260 * DAY,
  },
  {
    code: "LOYALTY-2026", description: "Five per cent standing discount for loyalty members.",
    percent_off: 5, amount_off_cents: null, max_redemptions: 2000, is_active: true,
    expires_offset: null,
  },
  {
    code: "RESTOCK-500", description: "Five euro off when a saved item comes back in.",
    percent_off: null, amount_off_cents: 500, max_redemptions: 400, is_active: true,
    expires_offset: 120 * DAY,
  },
  {
    code: "AUTUMN-20", description: "Twenty per cent off the autumn clearance, now closed.",
    percent_off: 20, amount_off_cents: null, max_redemptions: 250, is_active: false,
    expires_offset: -30 * DAY,
  },
  {
    code: "HOMEWARE-2500", description: "Twenty-five euro off homeware orders over two hundred.",
    percent_off: null, amount_off_cents: 2500, max_redemptions: 150, is_active: true,
    expires_offset: 90 * DAY,
  },
  {
    code: "COLLECT-FREE", description: "Delivery waived when collecting from a shop.",
    percent_off: null, amount_off_cents: 495, max_redemptions: 1000, is_active: true,
    expires_offset: 330 * DAY,
  },
];

const CARRIERS = ["Northline", "Parcelworks", "Fenway Freight", "Cityhop Couriers"];
const RETURN_REASONS = [
  "Too small once it arrived.",
  "Ordered two sizes and kept one.",
  "Arrived with a mark on the front panel.",
  "Changed mind within the cooling-off period.",
  "Not the colour shown on the listing.",
];

const TICKET_SUBJECTS = [
  "Where is my parcel?",
  "Wrong size delivered",
  "Coupon would not apply at checkout",
  "Gift card balance looks wrong",
  "Requesting a returns collection",
  "Invoice address needs correcting",
  "Missing item from a two-box order",
  "Question about the repair promise",
  "Card was charged twice",
  "Change of delivery address",
  "Spare buckle for a rucksack",
  "Loyalty points not credited",
];
const TICKET_STATUSES = ["open", "pending", "resolved", "closed"];
const TICKET_PRIORITIES = ["low", "normal", "normal", "high"];
const MSG_CUSTOMER_OPEN = [
  "Hello, I placed an order last week and I still have not had a dispatch note. Could you check where it is?",
  "Good morning. The parcel arrived today but one of the items is the wrong size. What is the easiest way to swap it?",
  "Hi, the code on your homepage would not apply at checkout. It kept saying the basket did not qualify.",
  "Afternoon. My gift card shows a smaller balance than I expected after one order. Could someone look at it?",
  "Hi there, I would like to send an item back. Do you arrange collection or should I post it myself?",
];
const MSG_AGENT_REPLY = [
  "Thanks for getting in touch. I can see the order on our side and it left the warehouse yesterday, so tracking should update this afternoon.",
  "Sorry about that. I have put a replacement aside in the size you wanted and I will send a prepaid returns slip for the first one.",
  "Apologies for the trouble. The code only covers the spring range, which is why the basket was not accepted. I have applied the discount manually instead.",
  "Good spot. One order drew on the card and the remainder is still there. I have attached the statement to this thread so you can see the movement.",
  "We can arrange a collection. If you confirm the address is unchanged, I will book it for the next working day.",
];
const MSG_CUSTOMER_FOLLOW = [
  "That is great, thank you. I will keep an eye on the tracking.",
  "Perfect, the address is the same as on the order.",
  "Thanks for sorting it so quickly.",
  "Understood. I will send the first one back once the slip arrives.",
  "Appreciate the explanation, that clears it up.",
];
const MSG_AGENT_CLOSE = [
  "Happy to help. I will leave this thread open for a few days in case anything else comes up.",
  "Noted and booked. You will get a confirmation from the courier by email.",
  "All done on our side. Do come back to us if the parcel has not moved by Friday.",
  "Marked as resolved. Thanks for your patience with this one.",
];

const ARTICLE_ROWS = [
  {
    slug: "delivery-times", title: "How long delivery takes", category: "Delivery",
    body: "Standard delivery runs two to four working days across the mainland and four to seven to the islands. Orders placed before three in the afternoon are picked the same day; anything later joins the next morning's run. Once the courier scans the parcel you will get a tracking link by email, and that link stays live for thirty days.",
  },
  {
    slug: "returns-window", title: "The returns window", category: "Returns",
    body: "You have thirty days from delivery to change your mind, and sixty days if the item was a gift. Items should come back unused and in their original packing where possible. Start the return from your account, print the slip, and drop the parcel at any collection point.",
  },
  {
    slug: "refund-timing", title: "When a refund lands", category: "Returns",
    body: "Refunds are raised the day your parcel is booked into the returns desk. Card refunds usually settle within three working days, though some banks take a full week to post them. If you paid partly with a gift card, that portion goes back to the card first.",
  },
  {
    slug: "gift-card-balance", title: "Checking a gift card balance", category: "Payments",
    body: "Gift card numbers are printed in three groups of four digits. Enter the number on the gift card page to see the remaining balance and the last five movements. Cards stay valid for two years from the date they were issued, and any balance left after an order stays on the card.",
  },
  {
    slug: "using-a-coupon", title: "Using a coupon code", category: "Payments",
    body: "Coupon codes go in the basket rather than at the payment step, so the total updates before you confirm. Only one code applies per order, and codes tied to a range only discount the qualifying lines. If a code is refused, check the expiry date printed alongside it.",
  },
  {
    slug: "tracking-an-order", title: "Tracking an order", category: "Orders",
    body: "Every order has a reference in the form of the year and a five-digit number. Sign in and open the order to see the current state, the courier and the tracking reference. If the state has not moved for two working days, contact support with the reference and we will chase it.",
  },
  {
    slug: "changing-an-address", title: "Changing a delivery address", category: "Orders",
    body: "An address can be changed until the order is packed, which is usually within a few hours of placing it. After that the courier can often redirect a parcel in transit. Addresses saved to your account can be edited at any time and only apply to future orders.",
  },
  {
    slug: "loyalty-points", title: "How loyalty points work", category: "Account",
    body: "You earn one point for every euro spent, credited when the order is delivered rather than when it is placed. Points can be put towards any order in blocks of five hundred. Tiers are reviewed monthly: eight hundred points moves you to silver and two thousand to gold.",
  },
  {
    slug: "repair-promise", title: "The five-year repair promise", category: "Account",
    body: "Anything with a seam, a strap or a moving part is covered for five years from purchase. Send us a photograph of the damage and we will either post the spare part or arrange a workshop repair. Wear from ordinary use is included; damage from alterations is not.",
  },
  {
    slug: "stock-notifications", title: "Being told when stock returns", category: "Account",
    body: "Saved items send a notification the moment the size or colour you wanted is back on the shelf. Notifications appear in your account and, if you have opted in, by email. Saved searches work the same way and can be narrowed by shelf, brand or price.",
  },
  {
    slug: "collect-in-shop", title: "Collecting from a shop", category: "Delivery",
    body: "All eight shops hold click-and-collect orders for seven days. Choose the shop at checkout and wait for the ready-to-collect message before travelling. Bring the order reference and something with your name on it.",
  },
  {
    slug: "payment-methods", title: "Payment methods we accept", category: "Payments",
    body: "We accept the major card networks, gift cards and account credit. Cards are held rather than charged until the order is packed, so a pending amount may sit on your statement for a day or two. Saved cards can be removed from the account page at any time.",
  },
];

const NOTIFICATION_KINDS = ["order", "delivery", "wishlist", "points", "support"];

const SAVED_SEARCH_ROWS = [
  { label: "Kitchen under 90", rule: "category:kitchen price<9000" },
  { label: "Outdoor bargains", rule: "category:outdoor price<15000" },
  { label: "Travel, well rated", rule: "category:travel rating>3" },
  { label: "Garden clearance", rule: "category:garden tag:clearance" },
  { label: "Cycling under 120", rule: "category:cycling price<12000" },
  { label: "Home, new in", rule: "category:home tag:new-in" },
  { label: "Workshop essentials", rule: "category:workshop price<7000" },
  { label: "Pets under 50", rule: "category:pets price<5000" },
  { label: "Top rated kitchen", rule: "category:kitchen rating>4" },
  { label: "Camp under 200", rule: "category:outdoor price<20000" },
  { label: "Everyday carry", rule: "category:travel price<8000" },
  { label: "Gifts under 40", rule: "price<4000 rating>3" },
];

const DEFAULT_WIDGETS = [
  { id: "orders", title: "Recent orders", size: "wide" },
  { id: "wishlist", title: "Saved for later", size: "narrow" },
  { id: "loyalty", title: "Points balance", size: "narrow" },
];

const BANNER_ROWS = [
  {
    slug: "spring-range", headline: "The spring range has landed",
    body: "Lighter jackets, seed trays and everything you need for the first dry weekend of the year.",
    cta_url: "/collections/garden",
  },
  {
    slug: "repair-promise", headline: "Five years, spares included",
    body: "Straps, buckles and liners are all replaceable. Send us a photograph and we will do the rest.",
    cta_url: "/pages/care",
  },
  {
    slug: "collect-in-shop", headline: "Collect from any of our eight shops",
    body: "Order before three in the afternoon and collect the next working day, delivery waived.",
    cta_url: "/stores",
  },
  {
    slug: "kitchen-restock", headline: "Kitchen restock",
    body: "Enamelled kettles, stoneware bowls and the boards that keep selling out are back on the shelf.",
    cta_url: "/collections/kitchen",
  },
  {
    slug: "gift-cards", headline: "Gift cards for the undecided",
    body: "Any amount between ten and five hundred euro, valid for two years, delivered by email or post.",
    cta_url: "/gift-cards",
  },
  {
    slug: "outdoor-weekend", headline: "Built for a wet weekend",
    body: "Waxed cotton, oiled canvas and hardware that will not seize after a night in the rain.",
    cta_url: "/collections/outdoor",
  },
];

/* -------------------------------------------------------------------------- */
/* Credentials                                                                 */
/* -------------------------------------------------------------------------- */

/**
 * Deterministic passphrase for one account.
 *
 * Shape follows the sign-up policy: three capitalised words, four digits and a symbol.
 * Derived per estate, so a credential from one estate is worthless on the next.
 */
export function accountPassword(seed, subject) {
  const rnd = rngFrom(`${seed}:credential:${subject}`);
  const words = [];
  while (words.length < 3) {
    const word = pickFrom(rnd, PASSPHRASE_WORDS);
    if (!words.includes(word)) words.push(word);
  }
  const digits = intBetween(rnd, 1000, 9999);
  return `${words.join("-")}-${digits}${pickFrom(rnd, PASSPHRASE_SYMBOLS)}`;
}

/**
 * Roster padded up to the size of the account block.
 *
 * `deriveIdentity` drops people whose mailbox local part collides, so it can return
 * slightly fewer than the number of accounts we seed. The spare names keep the block a
 * fixed size without disturbing the order of the derived entries.
 */
export function buildRoster(identity) {
  const roster = identity.roster.map((person) => ({ ...person }));
  const taken = new Set(roster.map((person) => person.email));
  const rnd = rngFrom(`${identity.seed}:roster-padding`);
  while (roster.length < ACCOUNT_COUNT) {
    const given = pickFrom(rnd, SPARE_GIVEN);
    const family = pickFrom(rnd, SPARE_FAMILY);
    const local = `${given}.${family}`.toLowerCase();
    let email = `${local}@${identity.domain}`;
    let suffix = 2;
    while (taken.has(email)) {
      email = `${local}${suffix}@${identity.domain}`;
      suffix += 1;
    }
    taken.add(email);
    roster.push({ given, family, name: `${given} ${family}`, email });
  }
  return roster;
}

/**
 * The three accounts whose identifiers are quoted outside the service.
 *
 * Exported so the reset script can print them without opening a connection.
 */
export function credentials(seedValue = config.deploySeed) {
  const identity = deriveIdentity(seedValue);
  const roster = buildRoster(identity);
  const account = (id, person, role) => ({
    id,
    role,
    email: person.email,
    name: person.name,
    given_name: person.given,
    family_name: person.family,
    password: accountPassword(identity.seed, String(id)),
  });
  return {
    seed: identity.seed,
    house: identity.houseName,
    domain: identity.domain,
    staff: account(STAFF_ID, roster[2], "staff"),
    primary: account(PRIMARY_ID, roster[0], "customer"),
    secondary: account(SECONDARY_ID, roster[1], "customer"),
  };
}

/* -------------------------------------------------------------------------- */
/* Dataset                                                                     */
/* -------------------------------------------------------------------------- */

function postcodeFor(rnd, place) {
  const letter = () => POSTCODE_LETTERS[Math.floor(rnd() * POSTCODE_LETTERS.length) % POSTCODE_LETTERS.length];
  switch (place.style) {
    case "gb":
      return `${letter()}${letter()}${intBetween(rnd, 1, 19)} ${intBetween(rnd, 1, 9)}${letter()}${letter()}`;
    case "nl":
      return `${intBetween(rnd, 1000, 9999)} ${letter()}${letter()}`;
    case "pt":
      return `${intBetween(rnd, 1000, 4999)}-${pad(intBetween(rnd, 0, 999), 3)}`;
    case "ie":
      return `${letter()}${intBetween(rnd, 10, 99)} ${letter()}${letter()}${intBetween(rnd, 10, 99)}`;
    case "num5":
      return String(intBetween(rnd, 10_000, 99_999));
    default:
      return String(intBetween(rnd, 1000, 9999));
  }
}

function phoneFor(rnd, place) {
  return `${place.dial} ${intBetween(rnd, 100, 999)} ${intBetween(rnd, 100, 999)} ${intBetween(rnd, 100, 999)}`;
}

function reviewBody(rnd, rating, title) {
  const openBank = rating >= 4 ? REVIEW_OPEN_GOOD : rating === 3 ? REVIEW_OPEN_MIXED : REVIEW_OPEN_POOR;
  const open = pickFrom(rnd, openBank).replace("{title}", title);
  const middle = pickFrom(rnd, REVIEW_MIDDLE);
  const close = rating >= 4 ? pickFrom(rnd, REVIEW_CLOSE_GOOD) : pickFrom(rnd, REVIEW_CLOSE_MIXED);
  return `${open} ${middle} ${close}`;
}

function contentPages(identity) {
  const house = identity.houseName;
  const domain = identity.domain;
  const pages = [
    {
      slug: "about", title: `About ${house}`,
      body: [
        `${house} began with two work tables, a sewing machine bought at auction and a short list of pieces that the founders could not find made well enough anywhere else. Twenty years on the list is longer, but the rule behind it has not changed: if we cannot make something better than what is already on the shelf, we do not make it at all.`,
        `Everything we sell is drawn up in our own workshop and made either there or by one of the eight makers we have worked with for years. We visit each of them at least twice a year, we pay on thirty days, and we publish the town each piece comes from on its listing.`,
        `We keep eight shops, each with a repair counter. Bring in anything we have sold and someone will look at it, fit a spare where one exists, and tell you honestly when a piece has reached the end of its life.`,
      ].join("\n\n"),
    },
    {
      slug: "delivery", title: "Delivery",
      body: [
        "Standard delivery is two to four working days on the mainland and four to seven to the islands, charged at four euro ninety-five and waived on orders over seventy-five euro. Orders placed before three in the afternoon are picked the same working day.",
        "Express delivery is next working day where the courier serves the postcode, ordered before midday. We do not ship on Sundays or public holidays, and a parcel booked on a Friday afternoon will move on Monday morning.",
        "You can also collect free of charge from any of our eight shops. We hold collection orders for seven days and send a message the moment the parcel is on the shelf behind the counter.",
      ].join("\n\n"),
    },
    {
      slug: "returns", title: "Returns and exchanges",
      body: [
        "You have thirty days from delivery to change your mind, and sixty days if the item was bought as a gift. Items should come back unused, with any tags still attached and in their original packing where you still have it.",
        "Start a return from your account and print the prepaid slip, or ask support to book a collection from the address the order went to. Exchanges are handled as a return plus a fresh order so that stock is not held while a parcel is in transit.",
        "Refunds are raised the day the parcel reaches our returns desk. Cards usually settle within three working days. Where an order was paid partly with a gift card, the gift card is topped back up first and the balance goes to the card.",
      ].join("\n\n"),
    },
    {
      slug: "terms", title: "Terms of sale",
      body: [
        `These terms cover every order placed with ${house} through this site, our shops and our telephone line. Placing an order is an offer to buy, and the contract begins when we send the dispatch confirmation.`,
        "Prices include value added tax at the prevailing rate and are shown in euro. We reserve the right to correct a price that has been published in error, in which case we will contact you before charging anything and you may cancel without penalty.",
        "Nothing in these terms affects your statutory rights, including the right to a repair, a replacement or a refund where goods are faulty or not as described. Any dispute is governed by the law of the country in which our registered office sits.",
      ].join("\n\n"),
    },
    {
      slug: "privacy", title: "Privacy notice",
      body: [
        `We collect the details we need to take an order and deliver it: your name, delivery and billing addresses, e-mail address, telephone number and order history. Where you hold an account with ${domain}, we also keep your saved addresses, saved searches and notification preferences.`,
        "Payment card numbers are handled by our payment provider and never reach our own systems; we hold only the network name, the last four digits and the expiry date so that you can recognise a saved card.",
        "We keep order records for six years to meet accounting obligations, and marketing preferences until you withdraw them. You can ask for a copy of what we hold, ask for a correction, or ask us to erase anything we are not required to keep, by writing to the address on the contact page.",
      ].join("\n\n"),
    },
    {
      slug: "cookies", title: "Cookies",
      body: [
        "We set a small number of cookies. The essential ones keep your basket and your signed-in session alive and cannot be switched off without breaking checkout.",
        "Preference cookies remember your currency, your language and whether you asked for the light or dark presentation. Measurement cookies count visits in aggregate so that we know which shelves people actually browse.",
        "You can change your choices at any time from the link in the footer, and clearing them in your browser will simply return everything to the defaults on your next visit.",
      ].join("\n\n"),
    },
    {
      slug: "sizing", title: "Sizing guide",
      body: [
        "Our sizing follows the measurements printed on each listing rather than a generic chart, because a jacket cut for layering and a jacket cut close to the body cannot share one size label.",
        "Measure a piece you already own flat on a table and compare it with the figures under the photographs. For bags and boxes we publish internal dimensions and the volume in litres, both measured with the piece empty.",
        "If you fall between two sizes, take the larger for outer layers and the smaller for anything worn against the body. Support will happily talk sizing through before you order.",
      ].join("\n\n"),
    },
    {
      slug: "care", title: "Care and repair",
      body: [
        "Waxed cotton wants a brush and cold water, never a machine. Rewax once a year, or twice if the piece lives outdoors, and dry it away from direct heat.",
        "Stoneware and enamel are dishwasher safe but last longer washed by hand. Cast iron should be dried on the hob and wiped with a trace of oil before it goes back in the cupboard.",
        `Anything with a seam, a strap or a moving part carries our five-year repair promise. Send a photograph to the repair counter address and we will post the spare part or arrange a workshop repair, whichever is quicker.`,
      ].join("\n\n"),
    },
    {
      slug: "careers", title: "Working with us",
      body: [
        `${house} employs a hundred and forty people across the workshop, the warehouse and the eight shops. We advertise every role publicly, we publish the salary band in the advert, and we do not ask for previous pay.`,
        "Shop roles come with a rota published three weeks ahead, a repair-counter apprenticeship after the first year, and the same discount for part-time and full-time colleagues alike.",
        "If nothing on the list suits, write to the address on the contact page with a note about what you would like to do. We keep speculative letters for six months and come back to them when a role opens.",
      ].join("\n\n"),
    },
    {
      slug: "contact", title: "Contact us",
      body: [
        `Support answers messages between nine and six on weekdays and nine and one on Saturdays. The quickest route is the form on this page, which opens a ticket with a reference in the form of two letters and four digits.`,
        `You can also write to support@${domain} or telephone the number printed on your dispatch note. Please quote your order reference so we can find the record before we call you back.`,
        "For press, wholesale and repair-counter enquiries, use the same form and pick the matching subject; those threads go to a separate desk and are usually answered within two working days.",
      ].join("\n\n"),
    },
  ];
  return pages;
}

/**
 * Compose every row in memory.
 *
 * Building the whole dataset before touching the database keeps the derived values
 * consistent: loyalty balances are the sum of the loyalty rows, product ratings are the
 * average of the published reviews, and coupon counters match the redemption rows.
 */
function buildDataset(identity, secrets) {
  const seed = identity.seed;
  const roster = buildRoster(identity);
  const tables = {};
  const put = (name, columns, rows) => {
    tables[name] = { columns, rows };
  };

  /* ---------------------------- shelves and makers ------------------------- */

  const categories = CATEGORIES.map((entry, index) => ({
    id: CATEGORY_FIRST_ID + index,
    slug: entry.slug,
    name: entry.name,
    position: index + 1,
  }));
  const categoryBySlug = new Map(categories.map((row) => [row.slug, row]));

  const brandRnd = rngFrom(`${seed}:brands`);
  const brandPool = [...BRAND_NAMES];
  const brands = [];
  for (let index = 0; index < 8; index += 1) {
    const chosen = brandPool.splice(Math.floor(brandRnd() * brandPool.length) % brandPool.length, 1)[0];
    brands.push({
      id: BRAND_FIRST_ID + index,
      slug: slugify(chosen),
      name: chosen,
      blurb: BRAND_BLURBS[index % BRAND_BLURBS.length],
    });
  }

  /* ------------------------------- accounts -------------------------------- */

  const accountRnd = rngFrom(`${seed}:accounts`);
  const customers = [];

  const makeAccount = (id, person, role, secret) => {
    const record = {
      id,
      email: person.email,
      password_hash: secret.hash,
      password_salt: secret.salt,
      given_name: person.given,
      family_name: person.family,
      display_name: person.name,
      phone: phoneFor(accountRnd, PLACES[id % PLACES.length]),
      role,
      loyalty_tier: "bronze",
      loyalty_points: 0,
      avatar_url: null,
      marketing_opt_in: accountRnd() < 0.45,
      created_at: null,
    };
    customers.push(record);
    return record;
  };

  const staff = makeAccount(STAFF_ID, roster[2], "staff", secrets.staff);
  staff.created_at = ts(-900 * DAY);
  staff.marketing_opt_in = false;
  staff.avatar_url = `/media/avatars/${STAFF_ID}.png`;

  const primary = makeAccount(PRIMARY_ID, roster[0], "customer", secrets.primary);
  primary.created_at = ts(-540 * DAY);
  primary.marketing_opt_in = true;
  primary.avatar_url = `/media/avatars/${PRIMARY_ID}.png`;

  const secondary = makeAccount(SECONDARY_ID, roster[1], "customer", secrets.secondary);
  secondary.created_at = ts(-410 * DAY);
  secondary.marketing_opt_in = false;

  for (let index = 3; index < ACCOUNT_COUNT; index += 1) {
    const id = CUSTOMER_FIRST_ID + index - 1; // roster[3] -> 1003
    const record = makeAccount(id, roster[index], "customer", secrets.shared);
    record.created_at = ts(-(700 - index * 4) * DAY - intBetween(accountRnd, 0, 20) * HOUR);
  }

  const customerIds = customers.filter((row) => row.role === "customer").map((row) => row.id);

  /* ------------------------------- addresses ------------------------------- */

  const addrRnd = rngFrom(`${seed}:addresses`);
  const addresses = [];
  const addressesByCustomer = new Map();
  let addressId = 0;
  for (const account of customers) {
    const count = account.id === PRIMARY_ID || account.id === SECONDARY_ID
      ? 2
      : intBetween(addrRnd, 1, 2);
    const list = [];
    for (let n = 0; n < count; n += 1) {
      addressId += 1;
      const place = PLACES[intBetween(addrRnd, 0, PLACES.length - 1)];
      const row = {
        id: addressId,
        customer_id: account.id,
        label: n === 0 ? "Home" : pickFrom(addrRnd, ADDRESS_LABELS.slice(1)),
        recipient: account.display_name,
        line1: `${intBetween(addrRnd, 1, 180)} ${pickFrom(addrRnd, STREETS)}`,
        line2: pickFrom(addrRnd, ADDRESS_LINE2),
        city: place.city,
        postcode: postcodeFor(addrRnd, place),
        country: place.country,
        is_default: n === 0,
      };
      addresses.push(row);
      list.push(row);
    }
    addressesByCustomer.set(account.id, list);
  }

  /* ---------------------------- payment methods ---------------------------- */

  const payRnd = rngFrom(`${seed}:payment-methods`);
  const paymentMethods = [];
  const paymentsByCustomer = new Map();
  let paymentId = 0;
  for (const account of customers) {
    const count = account.id === PRIMARY_ID ? 2 : account.id === SECONDARY_ID ? 1 : intBetween(payRnd, 0, 2);
    const list = [];
    for (let n = 0; n < count; n += 1) {
      paymentId += 1;
      const row = {
        id: paymentId,
        customer_id: account.id,
        brand: pickFrom(payRnd, CARD_BRANDS),
        last4: pad(intBetween(payRnd, 0, 9999), 4),
        exp_month: intBetween(payRnd, 1, 12),
        exp_year: intBetween(payRnd, 2027, 2032),
        is_default: n === 0,
      };
      paymentMethods.push(row);
      list.push(row);
    }
    paymentsByCustomer.set(account.id, list);
  }

  /* -------------------------------- catalogue ------------------------------ */

  const catRnd = rngFrom(`${seed}:catalogue-rows`);
  const products = [];
  for (let index = 0; index < PRODUCT_COUNT; index += 1) {
    const source = identity.products[index];
    const shelfRule = SHELF_RULES.find(([pattern]) => pattern.exec(source.title) !== null);
    const shelf = shelfRule ? categoryBySlug.get(shelfRule[1]) : categories[index % categories.length];
    const brand = brands[(index * 3 + Math.floor(index / 8)) % brands.length];
    const base = intBetween(catRnd, 1200, 24900);
    const price = Math.min(24900, Math.max(1200, Math.round(base / 100) * 100 - 5));
    products.push({
      id: PRODUCT_FIRST_ID + index,
      slug: source.slug,
      title: source.title,
      summary: pickFrom(catRnd, PRODUCT_SUMMARIES),
      description: [
        pickFrom(catRnd, DESC_OPEN).replace("{title}", source.title),
        pickFrom(catRnd, DESC_BODY),
        pickFrom(catRnd, DESC_CLOSE),
      ].join(" "),
      category_id: (shelf ?? categories[0]).id,
      brand_id: brand.id,
      price_cents: price,
      currency: "EUR",
      rating_avg: "0.00",
      rating_count: 0,
      tag: index < 6 ? "core" : pickFrom(catRnd, PRODUCT_TAGS),
      // The first six lines stay on sale: shop tooling quotes their variant numbers.
      is_active: index < 6 ? true : catRnd() > 0.06,
      created_at: ts(-(600 - index * 5) * DAY),
    });
  }
  // Every shelf has to hold stock. The derived titles occasionally miss a keyword group
  // altogether, in which case the fullest shelf lends the empty one a few lines.
  for (const shelf of categories) {
    if (products.some((row) => row.category_id === shelf.id)) continue;
    const tally = new Map();
    for (const row of products) tally.set(row.category_id, (tally.get(row.category_id) ?? 0) + 1);
    let fullest = categories[0].id;
    let best = -1;
    for (const entry of [...tally.entries()].sort((left, right) => left[0] - right[0])) {
      if (entry[1] > best) {
        best = entry[1];
        fullest = entry[0];
      }
    }
    const donors = products.filter((row) => row.category_id === fullest);
    for (let n = 0; n < 3 && n < donors.length - 1; n += 1) {
      donors[donors.length - 1 - n].category_id = shelf.id;
    }
  }

  const productById = new Map(products.map((row) => [row.id, row]));

  /* --------------------------------- variants ------------------------------ */

  const varRnd = rngFrom(`${seed}:variants`);
  const variants = [];
  let variantId = VARIANT_FIRST_ID - 1;
  for (let index = 0; index < products.length; index += 1) {
    const product = products[index];
    const brand = brands.find((row) => row.id === product.brand_id);
    const optionSet = OPTION_SETS[(index + product.category_id) % OPTION_SETS.length];
    // Two three-way lines up front keeps 3101 and 3107 pointing at stocked lines.
    const count = index < 2 ? 3 : intBetween(varRnd, 1, 3);
    for (let n = 0; n < count; n += 1) {
      variantId += 1;
      const stock = index < 6 ? intBetween(varRnd, 50, 400) : intBetween(varRnd, 0, 320);
      variants.push({
        id: variantId,
        product_id: product.id,
        sku: `${brand.slug.replace(/[^a-z0-9]/g, "").slice(0, 3).toUpperCase()}-${product.id}-${pad(n + 1, 2)}`,
        option_name: optionSet.name,
        option_value: optionSet.values[n % optionSet.values.length],
        price_cents: product.price_cents + n * intBetween(varRnd, 0, 12) * 100,
        stock,
      });
    }
  }
  const sellableVariants = variants.filter((variant) => {
    const product = productById.get(variant.product_id);
    return product.is_active && variant.stock > 0;
  });

  /* ---------------------------------- media -------------------------------- */

  const mediaRnd = rngFrom(`${seed}:media`);
  const media = [];
  let mediaId = 0;
  for (const product of products) {
    const count = intBetween(mediaRnd, 2, 4);
    for (let n = 0; n < count; n += 1) {
      mediaId += 1;
      media.push({
        id: mediaId,
        product_id: product.id,
        url: `/media/products/${product.slug}-${n + 1}.jpg`,
        alt: `${product.title} ${pickFrom(mediaRnd, MEDIA_VIEWS)}`,
        position: n,
      });
    }
  }

  /* --------------------------------- reviews ------------------------------- */

  const revRnd = rngFrom(`${seed}:reviews`);
  const reviews = [];
  const reviewPairs = new Set();
  let reviewId = 0;
  const addReview = (product, offsetIndex) => {
    let customerId = null;
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const candidate = customerIds[intBetween(revRnd, 0, customerIds.length - 1)];
      if (!reviewPairs.has(`${product.id}:${candidate}`)) {
        customerId = candidate;
        break;
      }
    }
    if (customerId === null) return;
    reviewPairs.add(`${product.id}:${customerId}`);
    reviewId += 1;
    const rating = pickFrom(revRnd, RATING_POOL);
    const titleBank = rating >= 4 ? REVIEW_TITLES_GOOD : rating === 3 ? REVIEW_TITLES_MIXED : REVIEW_TITLES_POOR;
    reviews.push({
      id: reviewId,
      product_id: product.id,
      customer_id: customerId,
      rating,
      title: pickFrom(revRnd, titleBank),
      body: reviewBody(revRnd, rating, product.title),
      status: revRnd() < 0.94 ? "published" : "pending",
      created_at: ts(-(320 - offsetIndex) * DAY + intBetween(revRnd, 0, 20) * HOUR),
    });
  };
  let reviewOffset = 0;
  for (const product of products) {
    const count = intBetween(revRnd, 3, 5);
    for (let n = 0; n < count; n += 1) {
      reviewOffset += 1;
      addReview(product, reviewOffset % 300);
    }
  }
  for (let index = 0; reviews.length < 320; index += 1) {
    reviewOffset += 1;
    addReview(products[index % products.length], reviewOffset % 300);
    if (index > products.length * 6) break;
  }

  const ratingTotals = new Map();
  for (const review of reviews) {
    if (review.status !== "published") continue;
    const bucket = ratingTotals.get(review.product_id) ?? { sum: 0, count: 0 };
    bucket.sum += review.rating;
    bucket.count += 1;
    ratingTotals.set(review.product_id, bucket);
  }
  for (const product of products) {
    const bucket = ratingTotals.get(product.id);
    if (!bucket || bucket.count === 0) continue;
    product.rating_count = bucket.count;
    product.rating_avg = (bucket.sum / bucket.count).toFixed(2);
  }

  /* ---------------------------------- carts -------------------------------- */

  const cartRnd = rngFrom(`${seed}:carts`);
  const cartOwners = [PRIMARY_ID, SECONDARY_ID];
  while (cartOwners.length < 10) {
    const candidate = customerIds[intBetween(cartRnd, 0, customerIds.length - 1)];
    if (!cartOwners.includes(candidate)) cartOwners.push(candidate);
  }
  const carts = [];
  const cartItems = [];
  let cartItemId = CART_ITEM_FIRST_ID - 1;
  const cartRows = [...cartOwners, null, null]; // two baskets left by signed-out visitors
  for (let index = 0; index < cartRows.length; index += 1) {
    const cartId = CART_FIRST_ID + index;
    const created = -(18 - index) * DAY;
    carts.push({
      id: cartId,
      token: `crt_${createHash("sha256").update(`${seed}:cart:${cartId}`).digest("hex").slice(0, 24)}`,
      customer_id: cartRows[index],
      currency: "EUR",
      meta: JSON.stringify({ channel: index % 3 === 0 ? "web" : "mobile", locale: "en-GB" }),
      created_at: ts(created),
      updated_at: ts(created + 3 * HOUR),
    });
    const itemCount = index === 0 ? 3 : intBetween(cartRnd, 1, 3);
    const used = new Set();
    for (let n = 0; n < itemCount; n += 1) {
      const variant = sellableVariants[intBetween(cartRnd, 0, sellableVariants.length - 1)];
      if (used.has(variant.id)) continue;
      used.add(variant.id);
      cartItemId += 1;
      cartItems.push({
        id: cartItemId,
        cart_id: cartId,
        variant_id: variant.id,
        quantity: intBetween(cartRnd, 1, 3),
        unit_price_cents: variant.price_cents,
        added_at: ts(created + n * HOUR),
      });
    }
  }

  /* --------------------------------- coupons ------------------------------- */

  const coupons = COUPON_ROWS.map((entry, index) => ({
    id: index + 1,
    code: entry.code,
    description: entry.description,
    percent_off: entry.percent_off,
    amount_off_cents: entry.amount_off_cents,
    max_redemptions: entry.max_redemptions,
    redemptions: 0,
    is_active: entry.is_active,
    expires_at: entry.expires_offset === null || entry.expires_offset === undefined
      ? null
      : ts(entry.expires_offset),
    created_by: STAFF_ID,
    created_at: ts(-(300 - index * 12) * DAY),
  }));
  const couponByCode = new Map(coupons.map((row) => [row.code, row]));
  const lockedRedemptions = new Set(
    COUPON_ROWS.filter((entry) => entry.fixed_redemptions === 0).map((entry) => entry.code),
  );

  /* ---------------------------- checkout sessions -------------------------- */

  const checkoutRnd = rngFrom(`${seed}:checkout`);
  const checkoutSessions = [];
  const checkoutCoupons = [];
  let checkoutCouponId = 0;
  const openCarts = carts.filter((cart) => cart.customer_id !== null).slice(0, 6);
  for (let index = 0; index < openCarts.length; index += 1) {
    const cart = openCarts[index];
    const address = (addressesByCustomer.get(cart.customer_id) ?? [])[0] ?? null;
    const payment = (paymentsByCustomer.get(cart.customer_id) ?? [])[0] ?? null;
    const sessionId = CHECKOUT_FIRST_ID + index;
    const express = index % 3 === 2;
    checkoutSessions.push({
      id: sessionId,
      cart_id: cart.id,
      customer_id: cart.customer_id,
      address_id: address ? address.id : null,
      payment_method_id: payment ? payment.id : null,
      shipping_method: express ? "express" : "standard",
      shipping_rate_cents: express ? 995 : 495,
      state: index === 0 ? "open" : pickFrom(checkoutRnd, ["open", "ready", "abandoned"]),
      created_at: ts(-(12 - index) * DAY),
    });
    if (index % 2 === 0) {
      checkoutCouponId += 1;
      checkoutCoupons.push({
        id: checkoutCouponId,
        session_id: sessionId,
        code: index === 0 ? "SPRING-15" : "SUMMER-10",
        applied_at: ts(-(12 - index) * DAY + 20 * MINUTE),
      });
    }
  }

  /* --------------------------------- orders -------------------------------- */

  const orderRnd = rngFrom(`${seed}:orders`);
  const orders = [];
  const orderItems = [];
  const orderTransitions = [];
  const shipments = [];
  const orderReturns = [];
  const couponRedemptions = [];
  let orderItemId = 0;
  let transitionId = 0;
  let shipmentId = 0;
  let returnId = 0;
  let redemptionId = 0;

  // The states here are the ones the order state machine in routes/orders.js knows about;
  // an estate seeded with anything else produces a book of trade the fulfilment console
  // cannot move, because no transition is defined from a state that does not exist.
  const CHAINS = {
    placed: ["created", "placed"],
    picking: ["created", "placed", "paid", "picking"],
    fulfilled: ["created", "placed", "paid", "picking", "fulfilled"],
    cancelled: ["created", "placed", "cancelled"],
    refunded: ["created", "placed", "paid", "picking", "fulfilled", "refunded"],
  };
  const actorFor = (toState, customerId) => {
    if (toState === "placed") return { subject: String(customerId), role: "customer" };
    if (toState === "paid" || toState === "fulfilled") return { subject: "system", role: "system" };
    return { subject: String(STAFF_ID), role: "staff" };
  };

  // A handful of repeat customers make the order list look like a real book of trade.
  const regulars = [PRIMARY_ID, PRIMARY_ID, PRIMARY_ID, SECONDARY_ID, SECONDARY_ID];

  for (let index = 0; index < ORDER_COUNT; index += 1) {
    const orderId = ORDER_FIRST_ID + index;
    let customerId;
    if (index === 0) customerId = PRIMARY_ID;
    else if (index === 1) customerId = SECONDARY_ID;
    else if (index % 11 === 0) customerId = regulars[(index / 11) % regulars.length | 0];
    else customerId = customerIds[intBetween(orderRnd, 0, customerIds.length - 1)];

    let state;
    if (index === 0) state = "fulfilled";
    else if (index === 1) state = "picking";
    else if (index < 34) state = pickFrom(orderRnd, ["fulfilled", "fulfilled", "fulfilled", "refunded", "cancelled"]);
    else if (index < 50) state = pickFrom(orderRnd, ["fulfilled", "picking", "picking"]);
    else state = pickFrom(orderRnd, ["placed", "placed", "picking"]);

    const placedOffset = -(300 - index * 5) * DAY - intBetween(orderRnd, 0, 20) * HOUR;
    const lineCount = intBetween(orderRnd, 1, 4);
    let subtotal = 0;
    const usedVariants = new Set();
    for (let n = 0; n < lineCount; n += 1) {
      const variant = sellableVariants[intBetween(orderRnd, 0, sellableVariants.length - 1)];
      if (usedVariants.has(variant.id)) continue;
      usedVariants.add(variant.id);
      const product = productById.get(variant.product_id);
      const quantity = intBetween(orderRnd, 1, 3);
      const lineTotal = quantity * variant.price_cents;
      subtotal += lineTotal;
      orderItemId += 1;
      orderItems.push({
        id: orderItemId,
        order_id: orderId,
        variant_id: variant.id,
        title: `${product.title} — ${variant.option_name}: ${variant.option_value}`,
        quantity,
        unit_price_cents: variant.price_cents,
        line_total_cents: lineTotal,
      });
    }

    const shipping = subtotal >= 7500 ? 0 : 495;
    let discount = 0;
    let couponCode = null;
    if (state !== "cancelled" && orderRnd() < 0.28) {
      couponCode = orderRnd() < 0.7 ? "SPRING-15" : "SUMMER-10";
      const coupon = couponByCode.get(couponCode);
      discount = coupon.percent_off
        ? Math.floor((subtotal * coupon.percent_off) / 100)
        : coupon.amount_off_cents ?? 0;
      if (discount > subtotal) discount = subtotal;
    }
    const total = subtotal + shipping - discount;
    const address = (addressesByCustomer.get(customerId) ?? [])[0] ?? null;

    orders.push({
      id: orderId,
      reference: `ORD-2026-${pad(orderId, 5)}`,
      customer_id: customerId,
      address_id: address ? address.id : null,
      state,
      currency: "EUR",
      subtotal_cents: subtotal,
      shipping_cents: shipping,
      discount_cents: discount,
      total_cents: total,
      placed_at: ts(placedOffset),
    });

    const chain = CHAINS[state];
    for (let step = 0; step < chain.length - 1; step += 1) {
      transitionId += 1;
      const actor = actorFor(chain[step + 1], customerId);
      orderTransitions.push({
        id: transitionId,
        order_id: orderId,
        from_state: chain[step],
        to_state: chain[step + 1],
        actor_subject: actor.subject,
        actor_role: actor.role,
        created_at: ts(placedOffset + step * 8 * HOUR),
      });
    }

    if (state === "picking" || state === "fulfilled" || state === "refunded") {
      shipmentId += 1;
      shipments.push({
        id: shipmentId,
        order_id: orderId,
        carrier: pickFrom(orderRnd, CARRIERS),
        tracking_ref: `TR${pad(intBetween(orderRnd, 0, 99_999_999), 8)}`,
        state: state === "picking" ? "in_transit" : "delivered",
        shipped_at: ts(placedOffset + 26 * HOUR),
      });
    }

    if (state === "refunded" || (state === "fulfilled" && orderRnd() < 0.08)) {
      returnId += 1;
      orderReturns.push({
        id: returnId,
        order_id: orderId,
        reason: pickFrom(orderRnd, RETURN_REASONS),
        state: state === "refunded" ? "completed" : pickFrom(orderRnd, ["requested", "approved"]),
        created_at: ts(placedOffset + 6 * DAY),
      });
    }

    if (couponCode && discount > 0 && !lockedRedemptions.has(couponCode)) {
      redemptionId += 1;
      couponRedemptions.push({
        id: redemptionId,
        coupon_id: couponByCode.get(couponCode).id,
        order_id: orderId,
        customer_id: customerId,
        code: couponCode,
        redeemed_at: ts(placedOffset + 5 * MINUTE),
      });
      couponByCode.get(couponCode).redemptions += 1;
    }
  }

  /* -------------------------------- support -------------------------------- */

  const supRnd = rngFrom(`${seed}:support`);
  const tickets = [];
  const messages = [];
  let messageId = 0;
  for (let index = 0; index < TICKET_COUNT; index += 1) {
    const ticketId = TICKET_FIRST_ID + index;
    let customerId;
    if (index === 0) customerId = PRIMARY_ID;
    else if (index === 1) customerId = SECONDARY_ID;
    else customerId = customerIds[intBetween(supRnd, 0, customerIds.length - 1)];
    const openedAt = -(240 - index * 4) * DAY - intBetween(supRnd, 0, 8) * HOUR;
    tickets.push({
      id: ticketId,
      reference: `CS-${ticketId}`,
      customer_id: customerId,
      subject: pickFrom(supRnd, TICKET_SUBJECTS),
      status: index < 40 ? pickFrom(supRnd, ["resolved", "closed"]) : pickFrom(supRnd, TICKET_STATUSES),
      priority: pickFrom(supRnd, TICKET_PRIORITIES),
      created_at: ts(openedAt),
    });
    const messageCount = intBetween(supRnd, 2, 5);
    for (let n = 0; n < messageCount; n += 1) {
      messageId += 1;
      const fromCustomer = n % 2 === 0;
      let body;
      if (n === 0) body = pickFrom(supRnd, MSG_CUSTOMER_OPEN);
      else if (fromCustomer) body = pickFrom(supRnd, MSG_CUSTOMER_FOLLOW);
      else if (n === messageCount - 1) body = pickFrom(supRnd, MSG_AGENT_CLOSE);
      else body = pickFrom(supRnd, MSG_AGENT_REPLY);
      messages.push({
        id: messageId,
        ticket_id: ticketId,
        author_kind: fromCustomer ? "customer" : "agent",
        author_subject: fromCustomer ? String(customerId) : String(STAFF_ID),
        body,
        created_at: ts(openedAt + n * 5 * HOUR),
      });
    }
  }

  /* ------------------------------- gift cards ------------------------------ */

  const giftRnd = rngFrom(`${seed}:gift-cards`);
  const giftCards = [
    {
      id: 1, code: "0000-0000-0001", face_value_cents: 2500, customer_id: PRIMARY_ID,
      state: "issued", issued_at: ts(-120 * DAY),
    },
    {
      id: 2, code: "4831-2205-7719", face_value_cents: 5000, customer_id: PRIMARY_ID,
      state: "issued", issued_at: ts(-96 * DAY),
    },
  ];
  const giftCodes = new Set(giftCards.map((row) => row.code));
  const faceValues = [1000, 2000, 2500, 5000, 7500, 10_000];
  for (let index = 2; index < GIFT_CARD_COUNT; index += 1) {
    let code = "";
    for (let attempt = 0; attempt < 50; attempt += 1) {
      const group = () => pad(intBetween(giftRnd, 0, 9999), 4);
      code = `${group()}-${group()}-${group()}`;
      if (!giftCodes.has(code)) break;
    }
    giftCodes.add(code);
    const holder = index % 5 === 0 ? null : customerIds[intBetween(giftRnd, 0, customerIds.length - 1)];
    giftCards.push({
      id: index + 1,
      code,
      face_value_cents: pickFrom(giftRnd, faceValues),
      customer_id: holder,
      state: holder === null ? "issued" : pickFrom(giftRnd, ["issued", "issued", "redeemed", "expired"]),
      issued_at: ts(-(200 - index * 3) * DAY),
    });
  }

  const walletCredits = [];
  let walletId = 0;
  for (const card of giftCards) {
    if (card.state !== "redeemed" || card.customer_id === null) continue;
    walletId += 1;
    walletCredits.push({
      id: walletId,
      customer_id: card.customer_id,
      gift_card_id: card.id,
      amount_cents: card.face_value_cents,
      memo: "Gift card added to account credit",
      created_at: ts(-60 * DAY + walletId * HOUR),
    });
  }
  for (const customerId of [PRIMARY_ID, SECONDARY_ID]) {
    walletId += 1;
    walletCredits.push({
      id: walletId,
      customer_id: customerId,
      gift_card_id: null,
      amount_cents: 500,
      memo: "Goodwill credit after a late delivery",
      created_at: ts(-40 * DAY + walletId * HOUR),
    });
  }

  /* -------------------------- lists, alerts, points ------------------------ */

  const listRnd = rngFrom(`${seed}:lists`);
  const wishlistOwners = [PRIMARY_ID, SECONDARY_ID];
  while (wishlistOwners.length < 30) {
    const candidate = customerIds[intBetween(listRnd, 0, customerIds.length - 1)];
    if (!wishlistOwners.includes(candidate)) wishlistOwners.push(candidate);
  }
  const wishlists = [];
  const wishlistItems = [];
  let wishlistItemId = 0;
  const WISHLIST_NAMES = ["Saved for later", "Gift ideas", "Kitchen refresh", "Camping list", "Birthday"];
  for (let index = 0; index < wishlistOwners.length; index += 1) {
    const id = index + 1;
    wishlists.push({
      id,
      customer_id: wishlistOwners[index],
      name: index === 0 ? "Saved for later" : pickFrom(listRnd, WISHLIST_NAMES),
      created_at: ts(-(180 - index * 3) * DAY),
    });
    const itemCount = intBetween(listRnd, 1, 4);
    const used = new Set();
    for (let n = 0; n < itemCount; n += 1) {
      const variant = sellableVariants[intBetween(listRnd, 0, sellableVariants.length - 1)];
      if (used.has(variant.id)) continue;
      used.add(variant.id);
      wishlistItemId += 1;
      wishlistItems.push({
        id: wishlistItemId,
        wishlist_id: id,
        variant_id: variant.id,
        added_at: ts(-(170 - index * 3) * DAY + n * HOUR),
      });
    }
  }

  const savedSearches = SAVED_SEARCH_ROWS.map((entry, index) => ({
    id: index + 1,
    customer_id: index === 0 || index === 1 ? PRIMARY_ID : index === 2 ? SECONDARY_ID
      : customerIds[intBetween(listRnd, 0, customerIds.length - 1)],
    label: entry.label,
    rule: entry.rule,
    created_at: ts(-(150 - index * 6) * DAY),
  }));

  const noteRnd = rngFrom(`${seed}:notifications`);
  const notifications = [];
  let notificationId = 0;
  for (let index = 0; index < 96; index += 1) {
    const order = orders[index % orders.length];
    const customerId = index < 6
      ? (index % 2 === 0 ? PRIMARY_ID : SECONDARY_ID)
      : customerIds[intBetween(noteRnd, 0, customerIds.length - 1)];
    const kind = pickFrom(noteRnd, NOTIFICATION_KINDS);
    const bodies = {
      order: `Order ${order.reference} has been confirmed and is being picked.`,
      delivery: `Order ${order.reference} is out for delivery today between nine and one.`,
      wishlist: "An item you saved is back in the size you wanted.",
      points: "Your points balance has been updated after a delivered order.",
      support: "Support has replied to one of your threads.",
    };
    notificationId += 1;
    const createdAt = -(120 - index) * DAY;
    notifications.push({
      id: notificationId,
      customer_id: customerId,
      kind,
      body: bodies[kind],
      read_at: noteRnd() < 0.6 ? ts(createdAt + 9 * HOUR) : null,
      created_at: ts(createdAt),
    });
  }

  const loyalty = [];
  let loyaltyId = 0;
  const balances = new Map();
  for (const order of orders) {
    if (order.state !== "fulfilled" && order.state !== "picking") continue;
    const points = Math.floor(order.total_cents / 100);
    if (points <= 0) continue;
    loyaltyId += 1;
    loyalty.push({
      id: loyaltyId,
      customer_id: order.customer_id,
      points,
      reason: `Points earned on order ${order.reference}`,
      created_at: order.placed_at,
    });
    balances.set(order.customer_id, (balances.get(order.customer_id) ?? 0) + points);
  }
  for (const [customerId, balance] of [...balances.entries()].sort((a, b) => a[0] - b[0])) {
    if (balance < 1500) continue;
    loyaltyId += 1;
    loyalty.push({
      id: loyaltyId,
      customer_id: customerId,
      points: -500,
      reason: "Points put towards an order",
      created_at: ts(-30 * DAY + (loyaltyId % 24) * HOUR),
    });
    balances.set(customerId, balance - 500);
  }
  for (const account of customers) {
    const balance = Math.max(0, balances.get(account.id) ?? 0);
    account.loyalty_points = balance;
    account.loyalty_tier = balance >= 2000 ? "gold" : balance >= 800 ? "silver" : "bronze";
  }

  /* ------------------------------- operations ------------------------------ */

  const imports = [
    {
      id: 1,
      source_url: `https://feeds.${identity.domain}/catalogue/2026-01.csv`,
      state: "complete",
      requested_by: STAFF_ID,
      rows_seen: 4120,
      created_at: ts(-45 * DAY),
    },
    {
      id: 2,
      source_url: `https://feeds.${identity.domain}/stock/2026-02.csv`,
      state: "complete",
      requested_by: STAFF_ID,
      rows_seen: 1896,
      created_at: ts(-18 * DAY),
    },
    {
      id: 3,
      source_url: `https://feeds.${identity.domain}/stock/2026-03.csv`,
      state: "queued",
      requested_by: STAFF_ID,
      rows_seen: 0,
      created_at: ts(-2 * DAY),
    },
  ];

  const storeRnd = rngFrom(`${seed}:stores`);
  const stores = [];
  const storeHours = [];
  const storeSlugs = new Set();
  let storeHourId = 0;
  for (let index = 0; index < identity.stores.length; index += 1) {
    const source = identity.stores[index];
    let slug = slugify(source.city);
    if (storeSlugs.has(slug)) slug = `${slug}-${index + 1}`;
    storeSlugs.add(slug);
    const place = PLACES.find((entry) => entry.city === source.city) ?? PLACES[0];
    stores.push({
      id: index + 1,
      slug,
      name: `${identity.houseName} ${source.city}`,
      city: source.city,
      street: source.street,
      phone: phoneFor(storeRnd, place),
    });
    for (let weekday = 1; weekday <= 6; weekday += 1) {
      storeHourId += 1;
      storeHours.push({
        id: storeHourId,
        store_id: index + 1,
        weekday,
        opens: weekday === 6 ? "10:00" : "09:00",
        closes: weekday === 6 ? "17:00" : weekday === 4 ? "20:00" : "18:00",
      });
    }
  }

  const pages = contentPages(identity).map((page, index) => ({
    id: index + 1,
    slug: page.slug,
    title: page.title,
    body: page.body,
    updated_at: ts(-(90 - index * 4) * DAY),
  }));

  const banners = BANNER_ROWS.map((entry, index) => ({
    id: index + 1,
    slug: entry.slug,
    headline: entry.headline,
    body: entry.body,
    cta_url: entry.cta_url,
    position: index + 1,
  }));

  const preferences = [PRIMARY_ID, SECONDARY_ID, STAFF_ID].map((customerId, index) => ({
    customer_id: customerId,
    locale: "en-GB",
    currency: "EUR",
    theme: index === 1 ? "light" : "system",
    widgets: JSON.stringify(DEFAULT_WIDGETS),
    updated_at: ts(-(20 - index) * DAY),
  }));

  const last = (rows) => (rows.length === 0 ? 0 : rows[rows.length - 1].id);
  const counters = [
    ["customers", CUSTOMER_LAST_ID],
    ["addresses", last(addresses)],
    ["payment_methods", last(paymentMethods)],
    ["reviews", last(reviews)],
    ["carts", CART_COUNTER_START],
    ["cart_items", last(cartItems)],
    ["checkout_sessions", CHECKOUT_COUNTER_START],
    ["checkout_coupons", last(checkoutCoupons)],
    ["orders", ORDER_FIRST_ID + ORDER_COUNT - 1],
    ["order_items", last(orderItems)],
    ["order_transitions", last(orderTransitions)],
    ["shipments", last(shipments)],
    ["order_returns", last(orderReturns)],
    ["coupons", last(coupons)],
    ["coupon_redemptions", last(couponRedemptions)],
    ["support_tickets", TICKET_FIRST_ID + TICKET_COUNT - 1],
    ["support_messages", last(messages)],
    ["gift_cards", last(giftCards)],
    ["wallet_credits", last(walletCredits)],
    ["wishlists", last(wishlists)],
    ["wishlist_items", last(wishlistItems)],
    ["saved_searches", last(savedSearches)],
    ["notifications", last(notifications)],
    ["loyalty_transactions", last(loyalty)],
    ["imports", IMPORT_COUNTER_START],
    ["media", last(media)],
    ["variants", last(variants)],
    ["products", PRODUCT_FIRST_ID + PRODUCT_COUNT - 1],
  ].map(([name, value]) => ({ name, value }));

  /* ------------------------------ table shapes ----------------------------- */

  put("categories", DIGEST_PROJECTION.categories, categories);
  put("brands", DIGEST_PROJECTION.brands, brands);
  put("customers", DIGEST_PROJECTION.customers, customers);
  put("addresses", DIGEST_PROJECTION.addresses, addresses);
  put("payment_methods", DIGEST_PROJECTION.payment_methods, paymentMethods);
  put("account_preferences", DIGEST_PROJECTION.account_preferences, preferences);
  put("products", DIGEST_PROJECTION.products, products);
  put("variants", DIGEST_PROJECTION.variants, variants);
  put("media", DIGEST_PROJECTION.media, media);
  put("reviews", DIGEST_PROJECTION.reviews, reviews);
  put("carts", DIGEST_PROJECTION.carts, carts);
  put("cart_items", DIGEST_PROJECTION.cart_items, cartItems);
  put("coupons", DIGEST_PROJECTION.coupons, coupons);
  put("checkout_sessions", DIGEST_PROJECTION.checkout_sessions, checkoutSessions);
  put("checkout_coupons", DIGEST_PROJECTION.checkout_coupons, checkoutCoupons);
  put("orders", DIGEST_PROJECTION.orders, orders);
  put("order_items", DIGEST_PROJECTION.order_items, orderItems);
  put("order_transitions", DIGEST_PROJECTION.order_transitions, orderTransitions);
  put("shipments", DIGEST_PROJECTION.shipments, shipments);
  put("order_returns", DIGEST_PROJECTION.order_returns, orderReturns);
  put("coupon_redemptions", DIGEST_PROJECTION.coupon_redemptions, couponRedemptions);
  put("support_tickets", DIGEST_PROJECTION.support_tickets, tickets);
  put("support_messages", DIGEST_PROJECTION.support_messages, messages);
  put("support_articles", DIGEST_PROJECTION.support_articles,
    ARTICLE_ROWS.map((entry, index) => ({ id: index + 1, ...entry })));
  put("gift_cards", DIGEST_PROJECTION.gift_cards, giftCards);
  put("wallet_credits", DIGEST_PROJECTION.wallet_credits, walletCredits);
  put("wishlists", DIGEST_PROJECTION.wishlists, wishlists);
  put("wishlist_items", DIGEST_PROJECTION.wishlist_items, wishlistItems);
  put("saved_searches", DIGEST_PROJECTION.saved_searches, savedSearches);
  put("notifications", DIGEST_PROJECTION.notifications, notifications);
  put("loyalty_transactions", DIGEST_PROJECTION.loyalty_transactions, loyalty);
  put("imports", DIGEST_PROJECTION.imports, imports);
  put("stores", DIGEST_PROJECTION.stores, stores);
  put("store_hours", DIGEST_PROJECTION.store_hours, storeHours);
  put("content_pages", DIGEST_PROJECTION.content_pages, pages);
  put("banners", DIGEST_PROJECTION.banners, banners);
  put("id_counters", DIGEST_PROJECTION.id_counters, counters);

  return tables;
}

/* -------------------------------------------------------------------------- */
/* Writing                                                                     */
/* -------------------------------------------------------------------------- */

// Postgres refuses a statement with more than 65535 bound parameters, and a very wide
// statement is slower to plan than two narrower ones, so batches are kept well under it.
const MAX_PARAMS = 40_000;
const MAX_ROWS_PER_STATEMENT = 500;

async function insertRows(exec, table, columns, rows) {
  if (rows.length === 0) return 0;
  const perStatement = Math.max(
    1,
    Math.min(MAX_ROWS_PER_STATEMENT, Math.floor(MAX_PARAMS / columns.length)),
  );
  for (let start = 0; start < rows.length; start += perStatement) {
    const chunk = rows.slice(start, start + perStatement);
    const params = [];
    const tuples = chunk.map((row) => {
      const placeholders = columns.map((column) => {
        params.push(row[column] === undefined ? null : row[column]);
        return `$${params.length}`;
      });
      return `(${placeholders.join(", ")})`;
    });
    await exec(
      `INSERT INTO ${table} (${columns.join(", ")}) VALUES ${tuples.join(", ")}`,
      params,
    );
  }
  return rows.length;
}

async function loadSchema() {
  return readFile(new URL("./schema.sql", import.meta.url), "utf8");
}

/**
 * Drop the schema, recreate it and write the seeded block.
 *
 * Runs on a single connection inside one transaction: the schema statements and the rows
 * either all land or none do, and nothing else can observe a half-built catalogue.
 */
export async function seed(options = {}) {
  const log = options.log ?? (() => {});
  const db = options.pool ? null : await import("../db.js");
  const pool = options.pool ?? db.pool;

  const seedValue = options.deploySeed ?? config.deploySeed;
  const identity = deriveIdentity(seedValue);

  // Four password derivations rather than a hundred and twenty-one: the three quoted
  // accounts get their own, the rest of the book shares one derived credential.
  log("deriving credentials");
  const [staffSecret, primarySecret, secondarySecret, sharedSecret] = await Promise.all([
    hashPassword(accountPassword(identity.seed, String(STAFF_ID)), saltFor(identity.seed, String(STAFF_ID))),
    hashPassword(accountPassword(identity.seed, String(PRIMARY_ID)), saltFor(identity.seed, String(PRIMARY_ID))),
    hashPassword(accountPassword(identity.seed, String(SECONDARY_ID)), saltFor(identity.seed, String(SECONDARY_ID))),
    hashPassword(accountPassword(identity.seed, "roster"), saltFor(identity.seed, "roster")),
  ]);

  log("composing rows");
  const tables = buildDataset(identity, {
    staff: staffSecret,
    primary: primarySecret,
    secondary: secondarySecret,
    shared: sharedSecret,
  });

  const schemaText = await loadSchema();
  const counts = {};
  const client = await pool.connect();
  try {
    await client.query("BEGIN");
    log("applying schema");
    await client.query(schemaText);
    const exec = (text, params) => client.query(text, params);
    for (const table of SEED_TABLES) {
      const shape = tables[table];
      if (!shape) throw new Error(`no rows composed for ${table}`);
      counts[table] = await insertRows(exec, table, shape.columns, shape.rows);
      log(`${table}: ${counts[table]}`);
    }
    await client.query("COMMIT");
  } catch (error) {
    try {
      await client.query("ROLLBACK");
    } catch {
      // The connection is already unusable; the original error is the useful one.
    }
    throw error;
  } finally {
    client.release();
  }

  const runQuery = options.sql ?? (db ? db.sql : null) ?? (async (text, params) => {
    const result = await pool.query(text, params);
    return result.rows;
  });
  const digest = await stateDigest(runQuery);
  log(`digest ${digest}`);
  return { digest, counts };
}

/* -------------------------------------------------------------------------- */
/* Digest                                                                      */
/* -------------------------------------------------------------------------- */

/** Order-independent, driver-independent rendering of one value. */
function canonical(value) {
  if (value === null || value === undefined) return "null";
  if (value instanceof Date) return JSON.stringify(value.toISOString());
  if (typeof value === "bigint") return JSON.stringify(value.toString());
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

/**
 * Stable fingerprint of the seeded state.
 *
 * Computed in the application on purpose rather than by a database aggregate: the row
 * hashing built into Postgres is not promised to be stable across major versions, and a
 * digest that moves when the engine is upgraded is worse than no digest at all.
 */
export async function stateDigest(sqlFn) {
  const run = sqlFn ?? (await import("../db.js")).sql;
  const hash = createHash("sha256");
  for (const table of SEED_TABLES) {
    const columns = DIGEST_PROJECTION[table];
    const orderBy = DIGEST_ORDER[table] ?? "id";
    const rows = await run(
      `SELECT ${columns.join(", ")} FROM ${table} ORDER BY ${orderBy}`,
      [],
    );
    hash.update(`#${table}:${rows.length}\n`);
    for (const row of rows) {
      hash.update(`${columns.map((column) => canonical(row[column])).join("\u001f")}\n`);
    }
  }
  return hash.digest("hex").slice(0, 16);
}

export default seed;
