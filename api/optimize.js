// api/optimize.js
const https = require('https');

const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY || '';
// Ingest agent that kicks off the whole pipeline (INGEST → SIGNALS → PROJECTIONS → OPTIMIZER)
const INGEST_AGENT_ID =
  process.env.INGEST_AGENT_ID || 'YOUR_INGEST_AGENT_ID_HERE';

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ''),
      method: options.method || 'POST',
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
    return res.status(405).json({ success: false, error: 'Method not allowed' });

  try {
    if (!SWARMNODE_KEY) {
      return res
        .status(500)
        .json({ success: false, error: 'SWARMNODE_API_KEY not configured' });
    }
    if (!INGEST_AGENT_ID) {
      return res
        .status(500)
        .json({ success: false, error: 'INGEST_AGENT_ID not configured' });
    }

    // Parse request body (handles object or string)
    let body = req.body;
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body || '{}');
      } catch (e) {
        body = {};
      }
    }

    const { csv, sport = 'nba', locked_player } = body || {};
    if (!csv) {
      return res
        .status(400)
        .json({ success: false, error: 'Missing CSV content' });
    }

    console.log('📤 Creating INGEST job for sport:', sport);

    const payload = {
      csv,
      sport,
    };

    if (locked_player && locked_player.last_name) {
      payload.locked_player = locked_player;
    }

    const url = `${SWARMNODE_BASE}/v1/agents/${INGEST_AGENT_ID}/jobs/`;

    const response = await makeRequest(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({ payload }),
    });

    console.log('INGEST job response status:', response.statusCode);

    if (response.statusCode !== 201 && response.statusCode !== 200) {
      console.error('Failed to create ingest job:', response.body);
      return res.status(500).json({
        success: false,
        error: 'Failed to create ingest job on SwarmNode',
      });
    }

    let data;
    try {
      data = JSON.parse(response.body);
    } catch (e) {
      console.error('Failed to parse ingest response:', e.message);
      return res
        .status(500)
        .json({ success: false, error: 'Invalid response from SwarmNode' });
    }

    const jobId = data.id;
    console.log('✅ Created INGEST job:', jobId);

    // This is the ID you later paste into “Learn from Results”
    return res.status(200).json({
      success: true,
      job_id: jobId,
      message: 'Optimization pipeline started',
    });
  } catch (error) {
    console.error('❌ Optimize error:', error);
    return res.status(500).json({ success: false, error: error.message });
  }
};
