// api/upload-nfl.js
const https = require('https');

function makeRequest(url, options, postData) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ""),
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
        success: false,
        error: 'Missing SWARMNODE_API_KEY or NFL_INGEST_AGENT_ID'
      });
    }

    // Parse body
    let body;
    if (req.body && typeof req.body === 'object' && !Buffer.isBuffer(req.body)) {
      body = req.body;
    } else if (Buffer.isBuffer(req.body)) {
      body = JSON.parse(req.body.toString('utf8'));
    } else if (typeof req.body === 'string') {
      body = JSON.parse(req.body);
    } else {
      return res.status(400).json({ success: false, error: 'Invalid request body' });
    }

    const csvText = body.csv || body.csvText || '';

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        success: false,
        error: 'Invalid CSV - too short or empty'
      });
    }

    console.log('\n=== NFL UPLOAD DEBUG ===');
    console.log('CSV length:', csvText.length);
    console.log('Body keys:', Object.keys(body));

    // Extract lock/exclude (same as NBA)
    const normalizeName = (name) =>
      typeof name === "string"
        ? name.replace(/\s+/g, " ").trim()
        : "";

    let lockedPlayerName = null;
    const lockKeys = ["lock", "locked_player", "lock_player", "locked_players"];
    
    for (const key of lockKeys) {
      const val = body[key];
      if (!val) continue;
      
      if (typeof val === "string" && val.trim()) {
        lockedPlayerName = normalizeName(val);
        break;
      } else if (Array.isArray(val) && val.length > 0) {
        lockedPlayerName = normalizeName(String(val[0]));
        break;
      }
    }

    let excludedPlayers = [];
    const excludeKeys = ["exclude", "excluded_players", "exclude_players"];
    
    for (const key of excludeKeys) {
      const val = body[key];
      if (!val) continue;
      
      if (typeof val === "string" && val.trim()) {
        excludedPlayers = val.split(",").map(s => normalizeName(s)).filter(Boolean);
        break;
      } else if (Array.isArray(val)) {
        excludedPlayers = val.map(s => normalizeName(String(s))).filter(Boolean);
        break;
      }
    }

    console.log('Locked player:', lockedPlayerName || 'none');
    console.log('Excluded players:', excludedPlayers.length > 0 ? excludedPlayers : 'none');

    const payload = {
      csv: csvText,
      sport: 'nfl',
      slate_date: new Date().toISOString().split('T')[0],
      locked_players: lockedPlayerName || null,
      excluded_players: excludedPlayers.length > 0 ? excludedPlayers : null
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify({
      agent_id: NFL_INGEST_AGENT_ID,
      payload: payload
    });

    console.log('Sending to SwarmNode...');

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
      console.log('✅ NFL pipeline started:', result.id || result.job_id);
      
      return res.status(200).json({
        success: true,
        message: '🏈 NFL optimization started',
        job_id: result.id || result.job_id,
        locked_player: lockedPlayerName,
        excluded_players: excludedPlayers
      });
    } else {
      return res.status(response.statusCode || 500).json({
        success: false,
        error: 'NFL agent failed',
        details: result
      });
    }

  } catch (error) {
    console.error('❌ Error:', error);
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
