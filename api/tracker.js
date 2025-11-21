// api/tracker.js
const https = require('https');

const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY || '';
const OPTIMIZER_AGENT_ID =
  process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3';

// These should mirror your PROJECTIONS weights
const CURRENT_WEIGHTS = {
  W_SALARY_PROXY: 0.35,
  W_MATCHUP: 0.25,
  W_PACE: 0.15,
  W_REST: 0.10,
  W_OPPORTUNITY: 0.10,
  W_SENTIMENT: 0.05,
};

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ''),
      method: options.method || 'GET',
      headers: options.headers || {},
    };

    const req = https.request(reqOptions, (res) => {
      let data = '';
      res.on('data', (chunk) => {
        data += chunk;
      });
      res.on('end', () => {
        resolve({ statusCode: res.statusCode, body: data });
      });
    });

    req.on('error', reject);
    if (options.body) {
      req.write(options.body);
    }
    req.end();
  });
}

module.exports = async (req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST')
    return res.status(405).json({ ok: false, error: 'Method not allowed' });

  try {
    // ---- Parse body (string or object) ----
    let rawBody = req.body;
    let body = {};

    if (typeof rawBody === 'string') {
      try {
        body = JSON.parse(rawBody || '{}');
      } catch (e) {
        body = {};
      }
    } else if (typeof rawBody === 'object' && rawBody !== null) {
      body = rawBody;
    }

    // Accept both snake_case and camelCase to be safe
    const optimizerJobId =
      (body.optimizer_job_id || body.optimizerJobId || '').trim();
    const slateDate = body.slate_date || body.slateDate || null;

    console.log('📥 TRACKER raw body:', rawBody);
    console.log('📥 TRACKER parsed body:', body);
    console.log('📌 Parsed optimizerJobId:', optimizerJobId);

    if (!optimizerJobId) {
      return res.status(400).json({
        ok: false,
        error: 'Missing optimizer_job_id',
      });
    }

    if (!SWARMNODE_KEY) {
      return res
        .status(500)
        .json({ ok: false, error: 'SWARMNODE_API_KEY not configured' });
    }

    // ---- Step 1: Fetch that specific OPTIMIZER job ----
    // Endpoint: GET /v1/agents/{agent_id}/jobs/{job_id}/
    const jobUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/${optimizerJobId}/`;
    console.log('Step 1: Fetching optimizer job from:', jobUrl);

    const jobResp = await makeRequest(jobUrl, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
      },
    });

    console.log('Job response status:', jobResp.statusCode);

    if (jobResp.statusCode !== 200) {
      console.error('Failed to fetch optimizer job:', jobResp.body);
      return res.status(500).json({
        ok: false,
        error: 'Failed to fetch optimizer job from SwarmNode',
        statusCode: jobResp.statusCode,
      });
    }

    let jobData;
    try {
      jobData = JSON.parse(jobResp.body);
    } catch (e) {
      console.error('Failed to parse job response:', e.message);
      return res
        .status(500)
        .json({ ok: false, error: 'Invalid response from SwarmNode' });
    }

    console.log('Job summary:', {
      id: jobData.id,
      status: jobData.status,
      has_output: !!jobData.output,
      has_return_value: !!jobData.return_value,
    });

    // Optimizer usually returns the lineup in output or return_value
    const rawOutput = jobData.output || jobData.return_value || {};
    const lineup = rawOutput.lineup || [];
    const stats = rawOutput.stats || {};
    const recommendations = rawOutput.recommendations || [];
    const lineupExport = rawOutput.lineup_export || null;

    if (!Array.isArray(lineup) || lineup.length === 0) {
      return res.status(200).json({
        ok: false,
        error: 'No lineup found on optimizer job',
        job_status: jobData.status,
      });
    }

    // For now just return lineup + weights.
    // Later we’ll POST actual scores + winning lineup back into a learner endpoint.
    return res.status(200).json({
      ok: true,
      optimizer_job_id: optimizerJobId,
      slate_date: slateDate,
      lineup,
      stats,
      recommendations,
      lineup_export: lineupExport,
      current_weights: CURRENT_WEIGHTS,
      learning_summary: {
        message:
          'Lineup and projections loaded. Enter actual scores & winning lineup in the UI to compute new weights.',
      },
    });
  } catch (error) {
    console.error('TRACKER error:', error);
    return res.status(500).json({ ok: false, error: error.message });
  }
};
