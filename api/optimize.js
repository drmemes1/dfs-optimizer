// api/optimize.js - FULL WORKING VERSION w/ MULTI-LOCK + MULTI-EXCLUDE
const https = require('https');

function makeRequest(url, options, postData) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ''),
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
  // ====== CORS ======
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  
  if (req.method !== 'POST') {
    return res.status(405).json({ success: false, error: 'Method not allowed' });
  }

  // ====== Debug ======
  console.log('=== REQUEST DEBUG ===');
  console.log('Content-Type:', req.headers['content-type']);
  console.log('Body type:', typeof req.body);
  console.log('Body is Buffer?', Buffer.isBuffer(req.body));

  try {
    // ====== Parse Body Safely ======
    let body;
    if (req.body && typeof req.body === 'object' && !Buffer.isBuffer(req.body)) {
      body = req.body;
    } else if (Buffer.isBuffer(req.body)) {
      const bodyStr = req.body.toString('utf8');
      body = JSON.parse(bodyStr);
    } else if (typeof req.body === 'string') {
      body = JSON.parse(req.body);
    } else {
      return res.status(400).json({
        success: false,
        error: 'Invalid request body'
      });
    }

    // ====== Environment Vars ======
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    if (!SWARMNODE_KEY) {
      return res.status(500).json({ success: false, error: 'Missing SWARMNODE_API_KEY' });
    }
    if (!INGEST_AGENT_ID) {
      return res.status(500).json({ success: false, error: 'Missing INGEST_AGENT_ID' });
    }

    // ====== Extract Base Data ======
    const csvText = body.csv || '';
    const sport = body.sport || 'nba';

    // ====== New: Normalize multi-lock and multi-exclude ======
    function normalizeNameList(value) {
      if (!value) return null;

      if (Array.isArray(value)) {
        return value.map(v => (v || '').toString().trim()).filter(v => v.length > 0);
      }

      if (typeof value === 'string') {
        return value
          .split(/[,;\n]+/)
          .map(v => v.trim())
          .filter(v => v.length > 0);
      }

      return null;
    }

    const lockedNames = normalizeNameList(
      body.locked_names || body.locked_player || body.lock_player
    );

    const excludeNames = normalizeNameList(
      body.exclude_names || body.exclude_player || body.exclude_players
    );

    console.log("Locked names:", lockedNames);
    console.log("Excluded names:", excludeNames);

    // ====== Validate CSV ======
    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        success: false,
        error: 'CSV is empty or too short'
      });
    }

    const firstLine = csvText.split("\n")[0].toLowerCase();
    if (!firstLine.includes('name') || !firstLine.includes('salary')) {
      return res.status(400).json({
        success: false,
        error: 'CSV must contain Name and Salary columns'
      });
    }

    console.log("✓ CSV validation passed");

    // ====== Build Payload for SwarmNode ======
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;

    const payload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv: csvText,
        sport: sport,
        locked_names: lockedNames || null,
        exclude_names: excludeNames || null
      }
    };

    const postData = JSON.stringify(payload);

    console.log("SwarmNode URL:", url);
    console.log("Payload size:", postData.length);

    // ====== Call SwarmNode ======
    const response = await makeRequest(
      url,
      {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${SWARMNODE_KEY}`,
          'Content-Type': 'application/json',
          'Content-Length': Buffer.byteLength(postData)
        }
      },
      postData
    );

    console.log("SwarmNode status:", response.statusCode);
    console.log("SwarmNode body preview:", response.body.substring(0, 200));

    // Detect HTML error page
    if (response.body.trim().startsWith("<") || response.body.includes("<!DOCTYPE")) {
      return res.status(502).json({
        success: false,
        error: "SwarmNode returned HTML error page",
        status: response.statusCode,
        preview: response.body.substring(0, 300)
      });
    }

    // Parse JSON
    let result;
    try {
      result = JSON.parse(response.body);
    } catch (e) {
      return res.status(502).json({
        success: false,
        error: "Could not parse SwarmNode response",
        body: response.body.substring(0, 500)
      });
    }

    // ====== Success ======
    if (response.statusCode >= 200 && response.statusCode < 300) {
      console.log("✓ Success! Job ID:", result.id || result.job_id);

      return res.status(200).json({
        success: true,
        message: `${sport.toUpperCase()} optimization started`,
        job_id: result.id || result.job_id,
        locked_names: lockedNames,
        exclude_names: excludeNames,
        swarmnode_link: "https://app.swarmnode.ai"
      });
    }

    // ====== Return SwarmNode error ======
    return res.status(response.statusCode).json({
      success: false,
      error: 'SwarmNode error',
      details: result
    });

  } catch (error) {
    console.error("❌ Error:", error.message);

    return res.status(500).json({
      success: false,
      error: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
