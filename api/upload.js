// api/upload.js
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
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    console.log('📤 Upload endpoint called');
    console.log('Method:', req.method);
    console.log('Headers:', JSON.stringify(req.headers));

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

    const csvText = req.body?.csv || '';
    const sport = req.body?.sport || 'nba';

    console.log('Sport:', sport);
    console.log('CSV length:', csvText.length);
    console.log('CSV preview:', csvText.substring(0, 200));

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
        sport: sport
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
    console.log('SwarmNode response body preview:', response.body.substring(0, 500));

    // Try to parse response
    let result;
    try {
      result = JSON.parse(response.body);
    } catch (parseError) {
      console.error('❌ Failed to parse SwarmNode response as JSON');
      console.error('Response body:', response.body);
      
      // Check if it's an HTML error page
      if (response.body.includes('<!DOCTYPE') || response.body.includes('<html')) {
        return res.status(502).json({
          ok: false,
          error: 'SwarmNode API returned HTML instead of JSON',
          details: 'This usually means the API endpoint is incorrect or unavailable',
          raw_response: response.body.substring(0, 500)
        });
      }
      
      return res.status(502).json({
        ok: false,
        error: 'Invalid response from SwarmNode',
        details: parseError.message,
        raw_response: response.body.substring(0, 500)
      });
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      console.log('✅ SwarmNode job created successfully');
      console.log('Job ID:', result.id || result.job_id);
      
      return res.status(200).json({
        ok: true,
        message: `${sport.toUpperCase()} optimization started`,
        job_id: result.id || result.job_id,
        execution_address: result.execution_address,
        agent_id: result.agent_id,
        swarmnode_link: 'https://app.swarmnode.ai'
      });
    } else {
      console.error('❌ SwarmNode returned error status:', response.statusCode);
      console.error('Error details:', result);
      
      return res.status(response.statusCode || 500).json({
        ok: false,
        error: 'SwarmNode API error',
        status: response.statusCode,
        details: result
      });
    }

  } catch (error) {
    console.error('❌ Upload endpoint error:', error);
    console.error('Error stack:', error.stack);
    
    return res.status(500).json({
      ok: false,
      error: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
