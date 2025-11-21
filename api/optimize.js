// api/optimize.js - Simplified with better error handling
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
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  
  if (req.method !== 'POST') {
    return res.status(405).json({ success: false, error: 'Method not allowed' });
  }

  // Log raw request for debugging
  console.log('=== REQUEST DEBUG ===');
  console.log('Content-Type:', req.headers['content-type']);
  console.log('Body type:', typeof req.body);
  console.log('Body is Buffer?', Buffer.isBuffer(req.body));
  
  try {
    // Parse body - Vercel auto-parses JSON but let's be safe
    let body;
    
    if (req.body && typeof req.body === 'object' && !Buffer.isBuffer(req.body)) {
      // Already parsed
      body = req.body;
      console.log('Body already parsed by Vercel');
    } else if (Buffer.isBuffer(req.body)) {
      // Buffer - convert to string then parse
      const bodyStr = req.body.toString('utf8');
      console.log('Body is Buffer, length:', bodyStr.length);
      body = JSON.parse(bodyStr);
    } else if (typeof req.body === 'string') {
      // String - parse it
      console.log('Body is string, length:', req.body.length);
      body = JSON.parse(req.body);
    } else {
      console.log('Body is:', req.body);
      return res.status(400).json({
        success: false,
        error: 'Invalid request body',
        debug: {
          bodyType: typeof req.body,
          bodyValue: req.body
        }
      });
    }

    console.log('Parsed body keys:', Object.keys(body));
    console.log('CSV length:', body.csv?.length || 0);
    console.log('Sport:', body.sport);
    console.log('Locked player:', body.locked_player);

    // Environment check
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    if (!INGEST_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: 'INGEST_AGENT_ID not configured'
      });
    }

    // Extract data
    const csvText = body.csv || '';
    const sport = body.sport || 'nba';
    const lockedPlayer = body.locked_player || null;

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        success: false,
        error: 'CSV is empty or too short',
        csv_length: csvText.length
      });
    }

    // Validate CSV format
    const firstLine = csvText.split('\n')[0].toLowerCase();
    if (!firstLine.includes('name') || !firstLine.includes('salary')) {
      return res.status(400).json({
        success: false,
        error: 'CSV must contain Name and Salary columns',
        first_line: firstLine.substring(0, 100)
      });
    }

    console.log('âœ… Validation passed, calling SwarmNode...');

    // Call SwarmNode
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    
// Support multiple exclude players (comma-separated string from UI)
let excludePlayers = null;

if (body.exclude_players) {
  if (Array.isArray(body.exclude_players)) {
    excludePlayers = body.exclude_players;
  } else if (typeof body.exclude_players === "string") {
    excludePlayers = body.exclude_players
      .split(",")
      .map(p => p.trim())
      .filter(p => p.length > 0);
  }
}

const payload = {
  agent_id: INGEST_AGENT_ID,
  payload: {
    csv: csvText,
    sport: sport,
    locked_player: lockedPlayer || null,
    exclude_players: excludePlayers || null
  }
};

    const postData = JSON.stringify(payload);
    console.log('SwarmNode URL:', url);
    console.log('Payload size:', postData.length, 'bytes');

    const response = await makeRequest(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      }
    }, postData);

    console.log('SwarmNode status:', response.statusCode);
    console.log('SwarmNode body preview:', response.body.substring(0, 200));

    // Check if HTML error page
    if (response.body.trim().startsWith('<') || response.body.includes('<!DOCTYPE')) {
      return res.status(502).json({
        success: false,
        error: 'SwarmNode returned HTML error page',
        status: response.statusCode,
        preview: response.body.substring(0, 300)
      });
    }

    // Parse response
    let result;
    try {
      result = JSON.parse(response.body);
    } catch (e) {
      return res.status(502).json({
        success: false,
        error: 'Could not parse SwarmNode response',
        status: response.statusCode,
        body: response.body.substring(0, 500)
      });
    }

    // Success
    if (response.statusCode >= 200 && response.statusCode < 300) {
      console.log('âœ… Success! Job ID:', result.id || result.job_id);
      
      return res.status(200).json({
        success: true,
        message: `${sport.toUpperCase()} optimization started`,
        job_id: result.id || result.job_id,
        total_salary: 'Processing...',
        total_projection: 'Processing...',
        lineup: 'Optimization in progress...',
        locked_player_used: lockedPlayer?.last_name || null,
        swarmnode_link: 'https://app.swarmnode.ai'
      });
    }

    // SwarmNode error
    return res.status(response.statusCode).json({
      success: false,
      error: 'SwarmNode error',
      status: response.statusCode,
      details: result
    });

  } catch (error) {
    console.error('âŒ Error:', error.message);
    console.error('Stack:', error.stack);
    
    return res.status(500).json({
      success: false,
      error: error.message,
      type: error.constructor.name,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
