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
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    if (!SWARMNODE_KEY || !INGEST_AGENT_ID) {
      return res.status(500).json({ 
        error: 'Missing configuration',
        details: 'Add SWARMNODE_API_KEY and INGEST_AGENT_ID in Vercel'
      });
    }

    let csvText = '';
    if (req.body && typeof req.body === 'object') {
      csvText = req.body.csv || req.body.csvText || '';
    }

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({ 
        error: 'Invalid CSV',
        details: 'CSV is empty or too short',
        length: csvText.length
      });
    }

    console.log(`📊 CSV received: ${csvText.length} chars`);
    console.log(`📝 First 100 chars: ${csvText.substring(0, 100)}`);

    // Send to INGEST agent - simplified payload structure
    const payload = {
      csv_text: csvText,
      inputs: {
        csv_text: csvText
      },
      data: {
        csv_text: csvText
      },
      slate_date: new Date().toISOString().split('T')[0]
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify({
      agent_id: INGEST_AGENT_ID,
      payload: payload
    });

    console.log('🚀 Calling INGEST agent:', INGEST_AGENT_ID);
    console.log('📦 Payload keys:', Object.keys(payload));

    const response = await makeRequest(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      }
    }, postData);

    console.log(`✅ SwarmNode responded: ${response.statusCode}`);

    let result;
    try {
      result = JSON.parse(response.body);
    } catch {
      result = { raw: response.body };
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return res.status(200).json({
        success: true,
        message: '✅ Pipeline started! INGEST → SIGNALS → PROJECTIONS → OPTIMIZER',
        job_id: result.job_id || result.id,
        agent_started: 'INGEST',
        csv_length: csvText.length,
        swarmnode_link: `https://app.swarmnode.ai`
      });
    } else {
      return res.status(response.statusCode || 500).json({
        error: 'SwarmNode API error',
        details: result
      });
    }

  } catch (error) {
    console.error('❌ Error:', error);
    return res.status(500).json({ 
      error: 'Server error',
      message: error.message
    });
  }
};
