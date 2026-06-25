// POST /api/unmark-used — reverts CA/READY company IN_USE → AVAILABLE
// Calls SECURITY DEFINER RPC; anon key stays server-side.

const SB_URL = process.env.SUPABASE_URL;
const SB_KEY = process.env.SUPABASE_ANON_KEY;
const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

module.exports = async (req, res) => {
  if (req.method !== 'POST') return res.status(405).end();

  let body = req.body;
  if (typeof body === 'string') { try { body = JSON.parse(body); } catch { body = {}; } }

  const { id } = body || {};
  if (!id || !UUID_RE.test(id)) {
    return res.status(400).json({ error: 'Invalid or missing company id' });
  }

  if (!SB_URL || !SB_KEY) return res.status(500).json({ error: 'Missing env vars' });

  try {
    const r = await fetch(`${SB_URL}/rest/v1/rpc/unmark_company_in_use`, {
      method: 'POST',
      headers: {
        apikey: SB_KEY,
        Authorization: `Bearer ${SB_KEY}`,
        'Content-Type': 'application/json',
        Accept: 'application/json',
      },
      body: JSON.stringify({ p_company_id: id }),
    });
    const data = await r.json();
    if (data && data.error) return res.status(400).json({ error: data.error });
    res.setHeader('Cache-Control', 'no-store');
    res.status(200).json({ ok: true });
  } catch (err) {
    console.error('unmark-used error:', err.message);
    res.status(500).json({ error: err.message });
  }
};
