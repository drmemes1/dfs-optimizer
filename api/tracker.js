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
    const TRACKER_AGENT_ID = process.env.TRACKER_AGENT_ID;
    const SPORTSDATA_API_KEY = process.env.SPORTSDATA_API_KEY;

    if (!SWARMNODE_KEY || !TRACKER_AGENT_ID) {
      return res.status(500).json({
        ok: false,
        error: 'Missing configuration',
        details: 'Add TRACKER_AGENT_ID to Vercel environment variables'
      });
    }

    const csvText = req.body?.csv || '';
    const slateDate = req.body?.slate_date || new Date().toISOString().split('T')[0];

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        ok: false,
        error: 'Invalid CSV'
      });
    }

    console.log(`📊 TRACKER: Processing results for ${slateDate}`);

    // Call TRACKER agent
    const payload = {
      results_csv: csvText,
      slate_date: slateDate,
      sportsdata_api_key: SPORTSDATA_API_KEY
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify({
      agent_id: TRACKER_AGENT_ID,
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
        message: `📈 TRACKER analyzing results for ${slateDate}`,
        job_id: result.job_id || result.id,
        swarmnode_link: 'https://app.swarmnode.ai'
      });
    } else {
      return res.status(response.statusCode || 500).json({
        ok: false,
        error: 'TRACKER agent failed',
        details: result
      });
    }

  } catch (error) {
    console.error('❌ TRACKER error:', error);
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
