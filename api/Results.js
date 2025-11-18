// api/results.js - Fetch results from SwarmNode
const https = require('https');

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ''),
      method: options.method || 'GET',
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
    req.end();
  });
}

module.exports = async (req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  
  if (req.method !== 'GET') {
    return res.status(405).json({ success: false, error: 'Method not allowed' });
  }

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    
    // Get job_id from query parameter
    const jobId = req.query.job_id;
    
    if (!jobId) {
      return res.status(400).json({
        success: false,
        error: 'Missing job_id parameter'
      });
    }

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'Server configuration error'
      });
    }

    console.log('Fetching results for job:', jobId);

    // Fetch job status from SwarmNode
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
    
    const response = await makeRequest(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('SwarmNode response status:', response.statusCode);

    // Parse response
    let result;
    try {
      result = JSON.parse(response.body);
    } catch (e) {
      return res.status(502).json({
        success: false,
        error: 'Could not parse SwarmNode response',
        details: e.message
      });
    }

    // Check job status
    const status = result.status || result.state;
    
    console.log('Job status:', status);
    console.log('Result keys:', Object.keys(result));

    if (status === 'completed' || status === 'success') {
      // Job is done - extract the lineup from the result
      const output = result.output || result.result || {};
      
      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: output.lineup || null,
        stats: output.stats || null,
        locked_player_used: output.locked_player_used || null,
        lineup_export: output.lineup_export || null,
        recommendations: output.recommendations || [],
        raw_output: output
      });
    } else if (status === 'running' || status === 'pending' || status === 'queued') {
      // Still processing
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Lineup optimization in progress...'
      });
    } else if (status === 'failed' || status === 'error') {
      // Job failed
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: result.error || 'Job failed',
        details: result
      });
    } else {
      // Unknown status
      return res.status(200).json({
        success: true,
        status: status || 'unknown',
        message: `Job status: ${status}`,
        raw_result: result
      });
    }

  } catch (error) {
    console.error('Results endpoint error:', error);
    
    return res.status(500).json({
      success: false,
      error: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
