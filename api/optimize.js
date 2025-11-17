// api/optimize.js
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
  // CORS headers
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  
  if (req.method !== 'POST') {
    return res.status(405).json({ error: 'Method not allowed' });
  }

  try {
    // Parse body if needed
    let body = req.body;
    if (typeof req.body === 'string') {
      try {
        body = JSON.parse(req.body);
      } catch (e) {
        return res.status(400).json({
          ok: false,
          error: 'Invalid JSON in request body'
        });
      }
    }

    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    console.log('📤 Optimize endpoint called');
    console.log('Method:', req.method);

    if (!SWARMNODE_KEY) {
      console.error('❌ Missing SWARMNODE_API_KEY');
      return res.status(500).json({
        ok: false,
        error: 'Server configuration error',
        details: 'SWARMNODE_API_KEY not set'
      });
    }

    if (!INGEST_AGENT_ID) {
      console.error('❌ Missing INGEST_AGENT_ID');
      return res.status(500).json({
        ok: false,
        error: 'Server configuration error',
        details: 'INGEST_AGENT_ID not set'
      });
    }

    const csvText = body?.csv || '';
    const sport = body?.sport || 'nba';
    const lockedPlayer = body?.locked_player || null;

    console.log('Sport:', sport);
    console.log('CSV length:', csvText.length);

    if (lockedPlayer && lockedPlayer.last_name) {
      console.log(`🔒 Locked player: ${lockedPlayer.last_name}${lockedPlayer.max_salary ? ` (max: $${lockedPlayer.max_salary})` : ''}`);
    }

    if (!csvText || csvText.length < 50) {
      console.error('❌ Invalid CSV');
      return res.status(400).json({
        ok: false,
        error: 'Invalid CSV data',
        details: 'CSV file appears to be empty or too short'
      });
    }

    // Validate CSV has proper headers
    const firstLine = csvText.split('\n')[0].toLowerCase();
    if (!firstLine.includes('name') || !firstLine.includes('salary')) {
      console.error('❌ Invalid CSV format - missing required columns');
      return res.status(400).json({
        ok: false,
        error: 'Invalid CSV format',
        details: 'CSV must contain Name and Salary columns'
      });
    }

    console.log('✅ CSV validation passed');
    console.log(`🚀 Calling INGEST agent: ${INGEST_AGENT_ID}`);

    // Call SwarmNode INGEST agent
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    
    const payload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv: csvText,
        sport: sport,
        locked_player: lockedPlayer
      }
    };

    const postData = JSON.stringify(payload);
    
    console.log('Payload size:', postData.length, 'bytes');

    const response = await makeRequest(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      }
    }, postData);

    console.log('SwarmNode response status:', response.statusCode);

    // Try to parse response
    let result;
    try {
      result = JSON.parse(response.body);
    } catch (parseError) {
      console.error('❌ Failed to parse SwarmNode response as JSON');
      
      if (response.body.includes('<!DOCTYPE') || response.body.includes('<html')) {
        return res.status(502).json({
          ok: false,
          error: 'SwarmNode API returned HTML instead of JSON',
          details: 'This usually means the API endpoint is incorrect or unavailable'
        });
      }
      
      return res.status(502).json({
        ok: false,
        error: 'Invalid response from SwarmNode',
        details: parseError.message
      });
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      console.log('✅ SwarmNode job created successfully');
      
      return res.status(200).json({
        success: true,
        message: `${sport.toUpperCase()} optimization started`,
        job_id: result.id || result.job_id,
        total_salary: 'Processing...',
        total_projection: 'Processing...',
        lineup: 'Optimization in progress...',
        locked_player_used: lockedPlayer?.last_name || null,
        execution_address: result.execution_address,
        agent_id: result.agent_id,
        swarmnode_link: 'https://app.swarmnode.ai'
      });
    } else {
      console.error('❌ SwarmNode returned error status:', response.statusCode);
      
      return res.status(response.statusCode || 500).json({
        ok: false,
        error: 'SwarmNode API error',
        status: response.statusCode,
        details: result
      });
    }

  } catch (error) {
    console.error('❌ Optimize endpoint error:', error);
    
    return res.status(500).json({
      ok: false,
      error: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
