// Vercel serverless function — CA companies (READY + PARTIAL + READY_NO_PDF)
// Uses Supabase REST API (PostgREST) with anon key server-side.
// RLS policies ensure only CA/READY+PARTIAL+READY_NO_PDF rows are visible to anon role.

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
      select: 'id,commercial_name,legal_name,entity_number,phone_e164,website_url,dossier_status,partial_reasons,usage_status,owner_first_name,owner_last_name',
      source_state: 'eq.CA',
      dossier_status: 'in.(READY,PARTIAL,READY_NO_PDF)',
      order: 'dossier_status.asc,commercial_name.asc',
    });

    if (!companies.length) {
      return res.status(200).json({ companies: [] });
    }

    const ids = companies.map((c) => c.id);
    const inIds = `in.(${ids.join(',')})`;

    // Identify READY/READY_NO_PDF companies that are IN_USE — need their USAGE_MARKED timestamp
    const inUseIds = companies
      .filter((c) => (c.dossier_status === 'READY' || c.dossier_status === 'READY_NO_PDF') && c.usage_status === 'IN_USE')
      .map((c) => c.id);

    const parallelQueries = [
      sbGet('company_addresses', {
        select: 'company_id,street_line1,suite,city,state,zip_code',
        company_id: inIds,
        address_type: 'eq.PRINCIPAL',
      }),
      sbGet('company_field_evidence', {
        select: 'company_id,field_name,field_value',
        company_id: inIds,
        'field_name': 'in.(email,website_email)',
        evidence_direction: 'eq.SUPPORTS',
      }),
      sbGet('company_documents', {
        select: 'company_id,storage_key',
        company_id: inIds,
        document_type: 'eq.SI',
        validation_status: 'eq.VALID',
      }),
    ];

    // Only query company_events if there are IN_USE companies
    if (inUseIds.length > 0) {
      parallelQueries.push(
        sbGet('company_events', {
          select: 'company_id,created_at',
          company_id: `in.(${inUseIds.join(',')})`,
          event_type: 'eq.USAGE_MARKED',
          order: 'created_at.desc',
        })
      );
    }

    const [addresses, emails, docs, usageEvents = []] = await Promise.all(parallelQueries);

    const addrMap = {};
    for (const a of addresses) if (!addrMap[a.company_id]) addrMap[a.company_id] = a;

    const emailMap = {};
    for (const e of emails) if (!emailMap[e.company_id]) emailMap[e.company_id] = e.field_value;

    const docMap = {};
    for (const d of docs) if (!docMap[d.company_id]) docMap[d.company_id] = d.storage_key;

    // Take the most recent USAGE_MARKED per company
    const markedAtMap = {};
    for (const ev of usageEvents) {
      if (!markedAtMap[ev.company_id]) markedAtMap[ev.company_id] = ev.created_at;
    }

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
        owner_name: [c.owner_first_name, c.owner_last_name].filter(Boolean).join(' ') || null,
        si_key: docMap[c.id] || null,
        usage_status: c.usage_status || 'AVAILABLE',
        marked_at: markedAtMap[c.id] || null,
      };
    });

    res.setHeader('Cache-Control', 'no-store');
    res.status(200).json({ companies: result });
  } catch (err) {
    console.error('companies error:', err.message);
    res.status(500).json({ error: err.message });
  }
};
