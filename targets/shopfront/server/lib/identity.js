/**
 * Deterministic content generation from DEPLOY_SEED.
 *
 * The same image is deployed to several estates (production, the partner preview, the
 * training estate that support uses for walkthroughs). They must not share a catalogue,
 * a customer list or a company name, and yet every estate has to be reproducible: a
 * support engineer replaying a bug report needs the same order 1001 they were sent.
 *
 * The compromise is that *identifiers* are fixed and *content* is derived. Numeric ids,
 * order references, coupon codes and gift card numbers are the same everywhere, because
 * they are what appears in a bug report; names, copy, addresses and credentials come out
 * of a seeded generator so that no two estates look alike.
 */
import { createHash } from "node:crypto";

/** 32-bit mixing PRNG seeded from a string. Small, stable across Node versions. */
export function rngFrom(seed) {
  const h = createHash("sha256").update(String(seed)).digest();
  let a = h.readUInt32LE(0) || 1;
  let b = h.readUInt32LE(4) || 2;
  let c = h.readUInt32LE(8) || 3;
  let d = h.readUInt32LE(12) || 4;
  return function next() {
    const t = (a + b) | 0;
    a = b ^ (b >>> 9);
    b = (c + (c << 3)) | 0;
    c = (c << 21) | (c >>> 11);
    d = (d + 1) | 0;
    const r = (t + d) | 0;
    c = (c + r) | 0;
    return (r >>> 0) / 4294967296;
  };
}

const pick = (rnd, list) => list[Math.floor(rnd() * list.length) % list.length];

const HOUSE = ["Kestrel", "Harrow", "Lundy", "Marlow", "Fenwick", "Alder", "Brackley",
  "Corvid", "Dunmore", "Everly", "Hartland", "Ivybridge", "Kelvin", "Norbury",
  "Peverell", "Quillon", "Rowanmoor", "Stannard", "Thornbury", "Wexley"];
const HOUSE_SUFFIX = ["Goods", "Supply Co", "Outfitters", "Provisions", "Trading Co",
  "Works", "Mercantile", "Field Supply"];

const GIVEN = ["Alice", "Rafael", "Nadia", "Tomas", "Ingrid", "Yusuf", "Marta", "Colm",
  "Sofia", "Piet", "Hana", "Emeka", "Lucia", "Anders", "Priya", "Gabriel", "Noor",
  "Katrin", "Dmitri", "Fiona", "Omar", "Beatriz", "Jonas", "Aiko", "Milan", "Zara",
  "Henrik", "Nuria", "Teodor", "Iva", "Callum", "Rosalind"];
const FAMILY = ["Moreau", "Delgado", "Okafor", "Lindqvist", "Novak", "Bassett", "Ferreira",
  "Halloran", "Kovac", "Wetherby", "Amrani", "Solberg", "Bianchi", "Pereira",
  "Nakamura", "Duarte", "Kaminski", "Vestergaard", "Rahimi", "Whitcombe", "Serrano",
  "Ilves", "Boateng", "Marchetti", "Ostrowski", "Fairbairn", "Nyholm", "Cabrera",
  "Reinholt", "Aguilar", "Brennan", "Kowalczyk"];

const MATERIAL = ["Waxed Cotton", "Merino", "Ripstop", "Oiled Canvas", "Recycled Nylon",
  "Cork", "Enamelled Steel", "Beech", "Stoneware", "Anodised Aluminium", "Linen",
  "Bridle Leather", "Cordura", "Borosilicate", "Hemp Twill", "Cast Iron"];
const OBJECT = ["Field Jacket", "Rucksack", "Camp Kettle", "Trail Flask", "Storm Cap",
  "Wash Bag", "Deck Chair", "Chopping Board", "Lantern", "Bike Pannier", "Tote",
  "Picnic Blanket", "Dog Lead", "Hip Flask", "Wall Hook", "Serving Bowl", "Kneeler",
  "Log Carrier", "Tool Roll", "Bird Feeder", "Watering Can", "Trug", "Apron",
  "Sleeping Mat", "Insulated Mug", "Tarpaulin", "Draught Excluder", "Plant Mister"];
const RANGE = ["Fell", "Harbourside", "Allotment", "Longshore", "Copse", "Weald",
  "Saltmarsh", "Drovers", "Hedgerow", "Boathouse", "Quarry", "Orchard"];

const CITY = ["Bristol", "Leeds", "Ghent", "Utrecht", "Aarhus", "Porto", "Girona",
  "Trieste", "Malmo", "Cork", "Nantes", "Bergen", "Tampere", "Ljubljana"];
const STREET = ["Wharf Road", "Kiln Lane", "Sowerby Street", "Millrace Walk",
  "Pentland Row", "Cobbold Street", "Hazelbank Road", "Tannery Yard", "Fold Street",
  "Northgate Crescent", "Ropewalk", "Quayside Terrace"];

const WORD = ["copper", "thistle", "harbour", "lantern", "meadow", "quarry", "willow",
  "pebble", "granite", "clover", "juniper", "cinder", "bracken", "saffron", "linden",
  "marram", "orchard", "rowan", "sable", "tamarisk"];

/** Everything derived from one seed, computed once at boot and at reset. */
export function deriveIdentity(seed) {
  const rnd = rngFrom(`${seed}:house`);
  const houseName = `${pick(rnd, HOUSE)} ${pick(rnd, HOUSE_SUFFIX)}`;
  const domain = `${houseName.toLowerCase().replace(/[^a-z0-9]+/g, "")}.example`;

  const people = [];
  const seen = new Set();
  const prnd = rngFrom(`${seed}:people`);
  while (people.length < 120) {
    const given = pick(prnd, GIVEN);
    const family = pick(prnd, FAMILY);
    const key = `${given} ${family}`;
    if (seen.has(key)) continue;
    seen.add(key);
    const local = `${given[0].toLowerCase()}.${family.toLowerCase().replace(/[^a-z]/g, "")}`;
    people.push({
      given,
      family,
      name: key,
      email: `${local}@${domain}`,
      // Passphrase in the shape the password policy asks for: three words, a number
      // and a symbol. Generated per estate so a leak from one is useless on the next.
      password: `${pick(prnd, WORD)}-${pick(prnd, WORD)}-${Math.floor(prnd() * 9000 + 1000)}`,
    });
  }
  // Distinct local parts only: two customers sharing an address would break sign-in.
  const byEmail = new Map();
  for (const p of people) if (!byEmail.has(p.email)) byEmail.set(p.email, p);
  const roster = [...byEmail.values()];

  const prod = rngFrom(`${seed}:catalogue`);
  const products = [];
  const usedSlug = new Set();
  for (let i = 0; products.length < 96 && i < 4000; i += 1) {
    const title = `${pick(prod, RANGE)} ${pick(prod, MATERIAL)} ${pick(prod, OBJECT)}`;
    const slug = title.toLowerCase().replace(/[^a-z0-9]+/g, "-");
    if (usedSlug.has(slug)) continue;
    usedSlug.add(slug);
    products.push({ title, slug });
  }

  const geo = rngFrom(`${seed}:geography`);
  const stores = [];
  for (let i = 0; i < 8; i += 1) {
    stores.push({
      city: CITY[(i * 3 + Math.floor(geo() * CITY.length)) % CITY.length],
      street: `${Math.floor(geo() * 180) + 1} ${pick(geo, STREET)}`,
    });
  }

  return { seed: String(seed), houseName, domain, roster, products, stores };
}

export default deriveIdentity;
