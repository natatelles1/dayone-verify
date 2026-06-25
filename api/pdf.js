// Vercel serverless function — R2 presigned URL (1-hour TTL)
// Generates AWS Signature V4 presigned URL using built-in crypto only.
// R2 credentials never reach the client.

const crypto = require('crypto');

// Safe key pattern: documents/<uuid>/<64-char-hex>.pdf
const KEY_PATTERN = /^documents\/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\/[0-9a-f]{64}\.pdf$/;

function sha256hex(data) {
  return crypto.createHash('sha256').update(data).digest('hex');
}

function hmac(key, data, enc) {
  return crypto.createHmac('sha256', key).update(data).digest(enc || undefined);
}

function presignR2(key) {
  const endpoint = process.env.R2_ENDPOINT;   // https://<acct>.r2.cloudflarestorage.com
  const bucket   = process.env.R2_BUCKET;
  const accessId = process.env.R2_ACCESS_KEY_ID;
  const secret   = process.env.R2_SECRET_ACCESS_KEY;
  const region   = 'auto';
  const service  = 's3';
  const expires  = 3600;

  const now = new Date();
  const amzDate  = now.toISOString().replace(/[-:]/g, '').replace(/\.\d{3}/, '');
  const datestamp = amzDate.slice(0, 8);

  // Key segments are hex+hyphens — no percent-encoding needed
  const canonicalPath = `/${bucket}/${key}`;
  const credentialScope = `${datestamp}/${region}/${service}/aws4_request`;

  const qsParams = {
    'X-Amz-Algorithm': 'AWS4-HMAC-SHA256',
    'X-Amz-Credential': `${accessId}/${credentialScope}`,
    'X-Amz-Date': amzDate,
    'X-Amz-Expires': String(expires),
    'X-Amz-SignedHeaders': 'host',
  };

  const host = new URL(endpoint).host;
  const canonicalQS = Object.keys(qsParams)
    .sort()
    .map((k) => `${encodeURIComponent(k)}=${encodeURIComponent(qsParams[k])}`)
    .join('&');

  const canonicalRequest = [
    'GET',
    canonicalPath,
    canonicalQS,
    `host:${host}\n`,
    'host',
    'UNSIGNED-PAYLOAD',
  ].join('\n');

  const stringToSign = [
    'AWS4-HMAC-SHA256',
    amzDate,
    credentialScope,
    sha256hex(canonicalRequest),
  ].join('\n');

  const kDate    = hmac(`AWS4${secret}`, datestamp);
  const kRegion  = hmac(kDate, region);
  const kService = hmac(kRegion, service);
  const kSigning = hmac(kService, 'aws4_request');
  const sig      = hmac(kSigning, stringToSign, 'hex');

  return `${endpoint}/${bucket}/${key}?${canonicalQS}&X-Amz-Signature=${sig}`;
}

module.exports = async (req, res) => {
  const { key } = req.query;

  if (!key || !KEY_PATTERN.test(key)) {
    return res.status(400).json({ error: 'Invalid or missing key' });
  }

  const required = ['R2_ENDPOINT', 'R2_BUCKET', 'R2_ACCESS_KEY_ID', 'R2_SECRET_ACCESS_KEY'];
  const missing = required.filter((v) => !process.env[v]);
  if (missing.length) {
    return res.status(500).json({ error: `Missing env vars: ${missing.join(', ')}` });
  }

  try {
    const url = presignR2(key);
    res.setHeader('Cache-Control', 'no-store');
    res.redirect(302, url);
  } catch (err) {
    console.error('pdf presign error:', err);
    res.status(500).json({ error: 'Failed to generate URL' });
  }
};
