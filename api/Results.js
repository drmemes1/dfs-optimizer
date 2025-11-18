// api/results.js - Get latest OPTIMIZER result (using return_value)
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

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const OPTIMIZER_AGENT_ID =
      process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3';

    const ingestJobId = req.query.job_id; // just for logging/debug

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured',
      });
    }

    console.log(`\n🔍 Fetching latest OPTIMIZER result (triggered by job: ${ingestJobId})`);

    // STEP 1: Get latest job for OPTIMIZER agent
    const listUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/?limit=1&ordering=-created_at`;
    console.log('Step 1: Listing latest job from:', listUrl);

    const listResponse = await makeRequest(listUrl, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
      },
    });

    console.log('List response status:', listResponse.statusCode);

    if (listResponse.statusCode !== 200) {
      console.error('Failed to list optimizer jobs:', listResponse.statusCode);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to complete...',
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResponse.body);
    } catch (e) {
      console.error('Failed to parse list response:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (list)',
      });
    }

    const jobs = listData.results || listData.jobs || [];
    console.log(`Found ${jobs.length} job(s)`);

    if (jobs.length === 0) {
      console.log('No optimizer jobs found yet');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...',
      });
    }

    const latestJobId = jobs[0].id;
    const latestJobStatus = jobs[0].status;
    console.log(`Latest job ID: ${latestJobId}, status: ${latestJobStatus}`);

    // STEP 2: Retrieve job details so we can read return_value
    const retrieveUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latestJobId}/`;
    console.log('Step 2: Retrieving job details from:', retrieveUrl);

    const retrieveResponse = await makeRequest(retrieveUrl, {
      method: 'GET',
      headers: {
        Authorization: `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
      },
    });

    console.log('Retrieve response status:', retrieveResponse.statusCode);

    if (retrieveResponse.statusCode !== 200) {
      console.error('Failed to retrieve job:', retrieveResponse.statusCode);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job still processing...',
      });
    }

    let jobDetails;
    try {
      jobDetails = JSON.parse(retrieveResponse.body);
    } catch (e) {
      console.error('Failed to parse job details:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid job response from SwarmNode',
      });
    }

    console.log('Job details retrieved:', {
      id: jobDetails.id,
      status: jobDetails.status,
      has_output: !!jobDetails.output,
      has_result: !!jobDetails.result,
      has_return_value: !!jobDetails.return_value,
    });

    const isCompleted =
      jobDetails.status === 'completed' || jobDetails.status === 'success';

    // IMPORTANT: SwarmNode puts your Python return dict here
    const returnValue =
      jobDetails.return_value || jobDetails.output || jobDetails.result || {};

    const hasLineup = returnValue.lineup && Array.isArray(returnValue.lineup);
    console.log('Return value has lineup:', hasLineup);

    if (isCompleted && hasLineup) {
      console.log(`✅ Found completed lineup! Job ID: ${jobDetails.id}`);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: returnValue.lineup || [],
        stats: returnValue.stats || {},
        locked_player_used: returnValue.locked_player_used || null,
        lineup_export: returnValue.lineup_export || null,
        recommendations: returnValue.recommendations || [],
        job_id: jobDetails.id,
        created_at: jobDetails.created_at,
      });
    }

    if (
      jobDetails.status === 'running' ||
      jobDetails.status === 'pending' ||
      jobDetails.status === 'queued'
    ) {
      console.log('Job still processing...');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...',
      });
    }

    if (jobDetails.status === 'failed' || jobDetails.status === 'error') {
      console.error('Job failed:', jobDetails.error);
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: jobDetails.error || 'Optimization failed',
      });
    }

    console.log('Job completed but no lineup yet / unknown status');
    return res.status(200).json({
      success: true,
      status: 'processing',
      message: 'Waiting for lineup data...',
      debug_status: jobDetails.status,
    });
  } catch (error) {
    console.error('❌ Error in results.js:', error.message);
    return res.status(500).json({
      success: false,
      error: error.message,
    });
  }
};
