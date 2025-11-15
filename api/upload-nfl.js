const https = require('https');

function makeRequest(url, options, postData) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname,
      method: options.method || 'POST',
      headers: options.headers || {}
    };
    
    const req = https.request(reqOptions, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        resolve({ statusCode: res.statusCode, body: data });
      });
    });
    
    req.on('error', reject);
    if (postData) req.write(postData);
    req.end();
  });
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const NFL_INGEST_AGENT_ID = process.env.NFL_INGEST_AGENT_ID;

    if (!SWARMNODE_KEY || !NFL_INGEST_AGENT_ID) {
      return res.status(500).json({
        ok: false,
        error: 'Missing NFL_INGEST_AGENT_ID'
      });
    }

    let csvText = '';
    if (req.body && typeof req.body === 'object') {
      csvText = req.body.csv || req.body.csvText || '';
    }

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        ok: false,
        error: 'Invalid CSV'
      });
    }

    const payload = {
      csv_text: csvText,
      slate_date: new Date().toISOString().split('T')[0],
      sport: 'NFL'
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify({
      agent_id: NFL_INGEST_AGENT_ID,
      payload: payload
    });

    const response = await makeRequest(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      }
    }, postData);

    let result;
    try {
      result = JSON.parse(response.body);
    } catch {
      result = { raw: response.body };
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return res.status(200).json({
        success: true,
        message: '🏈 NFL Pipeline started',
        job_id: result.job_id || result.id
      });
    } else {
      return res.status(response.statusCode || 500).json({
        ok: false,
        error: 'NFL agent failed',
        details: result
      });
    }

  } catch (error) {
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
