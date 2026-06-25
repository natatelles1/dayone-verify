// Vercel serverless function — CA companies (READY + PARTIAL)
// Direct Postgres connection via pg — credentials never reach the client.

const { Client } = require('pg');

const SQL = `
  SELECT
    c.id,
    c.commercial_name,
    c.legal_name,
    c.entity_number,
    c.phone_e164           AS phone,
    c.website_url          AS website,
    c.dossier_status,
    c.partial_reasons,
    ca.address_line_1,
    ca.address_line_2,
    ca.city,
    ca.state               AS addr_state,
    ca.zip_code,
    cfe.field_value        AS email,
    cd.storage_key         AS si_key
  FROM companies c
  LEFT JOIN company_addresses ca
         ON ca.company_id = c.id AND ca.address_type = 'PRINCIPAL'
  LEFT JOIN company_field_evidence cfe
         ON cfe.company_id = c.id
        AND cfe.field_name = 'email'
        AND cfe.evidence_direction = 'SUPPORTS'
  LEFT JOIN company_documents cd
         ON cd.company_id = c.id
        AND cd.document_type = 'SI'
        AND cd.validation_status = 'VALID'
  WHERE c.source_state = 'CA'
    AND c.dossier_status IN ('READY', 'PARTIAL')
  ORDER BY
    (c.dossier_status = 'PARTIAL'),
    c.commercial_name
`;

module.exports = async (req, res) => {
  if (!process.env.DATABASE_URL) {
    return res.status(500).json({ error: 'Missing DATABASE_URL env var' });
  }

  const client = new Client({
    connectionString: process.env.DATABASE_URL,
    ssl: { rejectUnauthorized: false },
    connectionTimeoutMillis: 8000,
  });

  try {
    await client.connect();
    const { rows } = await client.query(SQL);

    const companies = rows.map((r) => {
      const addrParts = [
        r.address_line_1,
        r.address_line_2,
        r.city && r.addr_state ? `${r.city}, ${r.addr_state}` : (r.city || r.addr_state),
        r.zip_code,
      ].filter(Boolean);

      return {
        id: r.id,
        commercial_name: r.commercial_name,
        legal_name: r.legal_name,
        entity_number: r.entity_number,
        address: addrParts.join(', ') || null,
        email: r.email || null,
        phone: r.phone || null,
        website: r.website || null,
        dossier_status: r.dossier_status,
        partial_reasons: r.partial_reasons || [],
        si_key: r.si_key || null,
      };
    });

    res.setHeader('Cache-Control', 's-maxage=120, stale-while-revalidate=30');
    res.status(200).json({ companies });
  } catch (err) {
    console.error('companies error:', err.message);
    res.status(500).json({ error: err.message });
  } finally {
    await client.end().catch(() => {});
  }
};
