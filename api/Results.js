// api/results.js - Get latest OPTIMIZER result
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
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3';
    
    const ingestJobId = req.query.job_id;
    
    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    console.log(`\n🔍 Fetching latest OPTIMIZER result (triggered by job: ${ingestJobId})`);

    // Get the latest job from OPTIMIZER agent
    // API: GET /v1/agents/{agent_id}/jobs/
    const url = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/?limit=1&ordering=-created_at`;
    
    console.log('Fetching latest optimizer job from:', url);

    const response = await makeRequest(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Response status:', response.statusCode);

    if (response.statusCode !== 200) {
      console.error('Failed to fetch optimizer jobs:', response.statusCode);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to complete...'
      });
    }

    let data;
    try {
      data = JSON.parse(response.body);
    } catch (e) {
      console.error('Failed to parse response');
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode'
      });
    }

    // SwarmNode returns jobs in 'results' or 'jobs' array
    const jobs = data.results || data.jobs || [];
    console.log(`Found ${jobs.length} job(s)`);

    if (jobs.length === 0) {
      console.log('No optimizer jobs found yet');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    // Get the latest job (should be only 1)
    const latestJob = jobs[0];
    
    console.log(`Latest job: ${latestJob.id}, status=${latestJob.status}`);

    // Check if completed with lineup
    const isCompleted = latestJob.status === 'completed' || latestJob.status === 'success';
    const hasLineup = latestJob.output?.lineup && Array.isArray(latestJob.output.lineup);

    if (isCompleted && hasLineup) {
      // Success!
      console.log(`✅ Found completed lineup! Job ID: ${latestJob.id}`);
      
      const output = latestJob.output || {};
      
      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: output.lineup || [],
        stats: output.stats || {},
        locked_player_used: output.locked_player_used || null,
        lineup_export: output.lineup_export || null,
        recommendations: output.recommendations || [],
        job_id: latestJob.id,
        created_at: latestJob.created_at
      });
    }

    // Check if still running
    if (latestJob.status === 'running' || latestJob.status === 'pending' || latestJob.status === 'queued') {
      console.log('Job still processing...');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    // Check if failed
    if (latestJob.status === 'failed' || latestJob.status === 'error') {
      console.error('Job failed:', latestJob.error);
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: latestJob.error || 'Optimization failed'
      });
    }

    // Unknown status
    console.log('Unknown job status:', latestJob.status);
    return res.status(200).json({
      success: true,
      status: 'processing',
      message: `Job status: ${latestJob.status}`
    });

  } catch (error) {
    console.error('❌ Error:', error.message);
    
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
