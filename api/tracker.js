// api/tracker.js
const https = require('https');

function fetchJson(url, apiKey) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);

    const options = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
      method: 'GET',
      headers: {
        'Content-Type': 'application/json',
        'x-api-key': apiKey
      }
    };

    const req = https.request(options, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => {
        resolve({ statusCode: res.statusCode, body: data });
      });
    });

    req.on('error', reject);
    req.end();
  });
}

module.exports = async (req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    // Vercel sometimes passes parsed JSON, sometimes string
    const body =
      typeof req.body === 'string'
        ? JSON.parse(req.body || '{}')
        : (req.body || {});

    console.log('📩 TRACKER raw body:', body);

    const { optimizer_job_id, slate_date } = body;

    if (!optimizer_job_id) {
      return res
        .status(400)
        .json({ error: 'Missing optimizer_job_id' });
    }

    console.log('📌 Parsed optimizerJobId:', optimizer_job_id);

    const apiKey = process.env.SWARMNODE_API_KEY;
    if (!apiKey) {
      return res
        .status(500)
        .json({ error: 'Missing SWARMNODE_API_KEY env var' });
    }

    // --- STEP 1: fetch that executor job from SwarmNode ---
    const jobUrl = `https://api.swarmnode.ai/v1/agent-executor-jobs/${optimizer_job_id}`;
    console.log('🔍 Fetching optimizer job from:', jobUrl);

    const jobResp = await fetchJson(jobUrl, apiKey);
    console.log('🔍 Job response status:', jobResp.statusCode);

    if (jobResp.statusCode !== 200) {
      console.error('❌ Failed to fetch optimizer job:', jobResp.body);
      return res.status(500).json({
        error: 'Failed to fetch optimizer job from SwarmNode',
        details: jobResp.body
      });
    }

    const jobData = JSON.parse(jobResp.body);
    console.log('📦 Job data:', jobData);

    const returnValue = jobData.return_value;
    if (!returnValue) {
      return res.status(400).json({
        error: 'Optimizer job exists but has no return_value yet'
      });
    }

    // In your optimizer, return_value already looks like:
    // { lineup, stats, recommendations, ... }
    const lineup = returnValue.lineup || null;
    const stats = returnValue.stats || null;
    const recommendations = returnValue.recommendations || null;

    console.log('✅ Extracted lineup from return_value');

    return res.status(200).json({
      ok: true,
      optimizer_job_id,
      slate_date,
      lineup,
      stats,
      recommendations,
      raw_return_value: returnValue
    });
  } catch (err) {
    console.error('❌ TRACKER error:', err);
    return res.status(500).json({
      error: 'Internal server error',
      details: err.message
    });
  }
};
