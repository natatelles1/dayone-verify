// GET /api/external/companies — external integration API (Gabriel, 1 client, static key).
// Server-to-server only (no CORS). Requires EXTERNAL_API_KEY as a Bearer token or x-api-key header.
// Returns ONLY CA companies with dossier_status IN (READY, READY_NO_PDF) — no PARTIAL/DISCOVERED,
// no internal fields (snapshot keys, evidence rows, match_score, jargon).
//
// Uses the Supabase anon key server-side (same key/pattern as /api/companies.js), gated by the
// dashboard_anon_ca_* RLS policies. Authorization for THIS endpoint is the EXTERNAL_API_KEY check
// below, not RLS — RLS here is just the same defense-in-depth already in place for the dashboard.
// Note: SUPABASE_SERVICE_ROLE_KEY is the new sb_secret_ format, which does not authenticate
// against PostgREST (verified: returns 401) — only the anon (JWT) key works here.

const crypto = require('crypto');

const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_ANON_KEY;
const API_KEY = process.env.EXTERNAL_API_KEY;

const RATE_LIMIT_WINDOW_MS = 60 * 1000;
const RATE_LIMIT_MAX = 30; // requests per IP per minute (best-effort, per warm instance)
const hits = new Map();

function isRateLimited(ip) {
  const now = Date.now();
  const recent = (hits.get(ip) || []).filter((t) => now - t < RATE_LIMIT_WINDOW_MS);
  recent.push(now);
  hits.set(ip, recent);
  return recent.length > RATE_LIMIT_MAX;
}

function extractKey(req) {
  const auth = req.headers['authorization'] || '';
  if (auth.startsWith('Bearer ')) return auth.slice(7).trim();
  return req.headers['x-api-key'] || null;
}

function isValidKey(provided) {
  if (!provided || !API_KEY) return false;
  const a = Buffer.from(provided);
  const b = Buffer.from(API_KEY);
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

async function sbGet(table, params) {
  const url = new URL(`${SB_URL}/rest/v1/${table}`);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  const r = await fetch(url.toString(), {
    headers: {
      apikey: SB_KEY,
      Authorization: `Bearer ${SB_KEY}`,
      Accept: 'application/json',
    },
  });
  if (!r.ok) {
    const body = await r.text();
    throw new Error(`${table}: HTTP ${r.status} — ${body}`);
  }
  return r.json();
}

module.exports = async (req, res) => {
  if (req.method !== 'GET') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  if (!SB_URL || !SB_KEY || !API_KEY) {
    return res.status(500).json({ error: 'Server misconfigured' });
  }

  const ip = (req.headers['x-forwarded-for'] || req.socket?.remoteAddress || 'unknown')
    .split(',')[0]
    .trim();
  if (isRateLimited(ip)) {
    return res.status(429).json({ error: 'Rate limit exceeded' });
  }

  if (!isValidKey(extractKey(req))) {
    return res.status(401).json({ error: 'Unauthorized' });
  }

  try {
    const companies = await sbGet('companies', {
      select:
        'id,legal_name,entity_number,phone_e164,website_url,dossier_status,owner_first_name,owner_last_name',
      source_state: 'eq.CA',
      dossier_status: 'in.(READY,READY_NO_PDF)',
      order: 'legal_name.asc',
    });

    if (!companies.length) {
      res.setHeader('Cache-Control', 'no-store');
      return res.status(200).json({ companies: [], count: 0 });
    }

    const ids = companies.map((c) => c.id);
    const inIds = `in.(${ids.join(',')})`;

    const [addresses, emails] = await Promise.all([
      sbGet('company_addresses', {
        select: 'company_id,street_line1,suite,city,state,zip_code',
        company_id: inIds,
        address_type: 'eq.PRINCIPAL',
      }),
      sbGet('company_field_evidence', {
        select: 'company_id,field_value',
        company_id: inIds,
        field_name: 'in.(email,website_email)',
        evidence_direction: 'eq.SUPPORTS',
      }),
    ]);

    const addrMap = {};
    for (const a of addresses) if (!addrMap[a.company_id]) addrMap[a.company_id] = a;

    const emailMap = {};
    for (const e of emails) if (!emailMap[e.company_id]) emailMap[e.company_id] = e.field_value;

    const result = companies.map((c) => {
      const addr = addrMap[c.id] || {};
      const addrParts = [
        addr.street_line1,
        addr.suite,
        addr.city && addr.state ? `${addr.city}, ${addr.state}` : addr.city || addr.state,
        addr.zip_code,
      ].filter(Boolean);

      return {
        legal_name: c.legal_name,
        owner_name: [c.owner_first_name, c.owner_last_name].filter(Boolean).join(' ') || null,
        email: emailMap[c.id] || null,
        phone: c.phone_e164 || null,
        address: addrParts.join(', ') || null,
        entity_number: c.entity_number,
        website: c.website_url || null,
        city: addr.city || null,
        status: c.dossier_status,
        // No populated timestamp source exists yet for "when this company became ready"
        // (schema has an unused dossier_completed_at column; the event history that would
        // give an accurate value isn't readable under the current anon-key RLS policies).
        // Reserved field — always null until that's wired up.
        verified_at: null,
      };
    });

    res.setHeader('Cache-Control', 'no-store');
    res.status(200).json({ companies: result, count: result.length });
  } catch (err) {
    console.error('external/companies error:', err.message);
    res.status(500).json({ error: 'Internal error' });
  }
};
