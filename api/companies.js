// Vercel serverless function — CA companies (READY + PARTIAL)
// Uses Supabase REST API (PostgREST) with anon key server-side.
// RLS policies ensure only CA/READY+PARTIAL rows are visible to anon role.

const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_ANON_KEY;

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
  if (!SB_URL || !SB_KEY) {
    return res.status(500).json({ error: 'Missing Supabase env vars' });
  }

  try {
    const companies = await sbGet('companies', {
      select: 'id,commercial_name,legal_name,entity_number,phone_e164,website_url,dossier_status,partial_reasons',
      source_state: 'eq.CA',
      dossier_status: 'in.(READY,PARTIAL)',
      order: 'dossier_status.asc,commercial_name.asc',
    });

    if (!companies.length) {
      return res.status(200).json({ companies: [] });
    }

    const ids = companies.map((c) => c.id);
    const inIds = `in.(${ids.join(',')})`;

    const [addresses, emails, docs] = await Promise.all([
      sbGet('company_addresses', {
        select: 'company_id,street_line1,suite,city,state,zip_code',
        company_id: inIds,
        address_type: 'eq.PRINCIPAL',
      }),
      sbGet('company_field_evidence', {
        select: 'company_id,field_value',
        company_id: inIds,
        field_name: 'eq.email',
        evidence_direction: 'eq.SUPPORTS',
      }),
      sbGet('company_documents', {
        select: 'company_id,storage_key',
        company_id: inIds,
        document_type: 'eq.SI',
        validation_status: 'eq.VALID',
      }),
    ]);

    const addrMap = {};
    for (const a of addresses) if (!addrMap[a.company_id]) addrMap[a.company_id] = a;

    const emailMap = {};
    for (const e of emails) if (!emailMap[e.company_id]) emailMap[e.company_id] = e.field_value;

    const docMap = {};
    for (const d of docs) if (!docMap[d.company_id]) docMap[d.company_id] = d.storage_key;

    const result = companies.map((c) => {
      const addr = addrMap[c.id] || {};
      const addrParts = [
        addr.street_line1,
        addr.suite,
        addr.city && addr.state ? `${addr.city}, ${addr.state}` : addr.city || addr.state,
        addr.zip_code,
      ].filter(Boolean);

      return {
        id: c.id,
        commercial_name: c.commercial_name,
        legal_name: c.legal_name,
        entity_number: c.entity_number,
        address: addrParts.join(', ') || null,
        email: emailMap[c.id] || null,
        phone: c.phone_e164 || null,
        website: c.website_url || null,
        dossier_status: c.dossier_status,
        partial_reasons: c.partial_reasons || [],
        si_key: docMap[c.id] || null,
      };
    });

    res.setHeader('Cache-Control', 's-maxage=120, stale-while-revalidate=30');
    res.status(200).json({ companies: result });
  } catch (err) {
    console.error('companies error:', err.message);
    res.status(500).json({ error: err.message });
  }
};
