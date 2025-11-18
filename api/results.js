// api/results.js - Retrieve latest OPTIMIZER lineup from SwarmNode
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

    const ingestJobId = req.query.job_id; // just for logging / display

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    console.log(`\n🔍 Checking OPTIMIZER results (triggered by job: ${ingestJobId || 'n/a'})`);

    // -------------------------------------------------------------------
    // STEP 1: List latest job for this OPTIMIZER agent
    // -------------------------------------------------------------------
    const listUrl =
      `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/?limit=1&ordering=-created_at`;

    console.log('Step 1: Listing latest OPTIMIZER job from:', listUrl);

    const listResponse = await makeRequest(listUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('List response status:', listResponse.statusCode);

    if (listResponse.statusCode !== 200) {
      console.error('Failed to list optimizer jobs:', listResponse.statusCode);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResponse.body);
    } catch (e) {
      console.error('Failed to parse job list response', e);
      return res.status(502).json({
        success: false,
        error: 'Invalid job list response from SwarmNode'
      });
    }

    const jobs = listData.results || listData.jobs || [];
    console.log(`Found ${jobs.length} job(s)`);

    if (jobs.length === 0) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    const latestJob = jobs[0];
    const latestJobId = latestJob.id;
    console.log(`Latest job: ${latestJobId}, status=${latestJob.status}`);

    // -------------------------------------------------------------------
    // STEP 2: Retrieve full job to get RETURN VALUE (lineup)
    // -------------------------------------------------------------------
    const retrieveUrl =
      `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latestJobId}/`;

    console.log('Step 2: Retrieving job details from:', retrieveUrl);

    const retrieveResponse = await makeRequest(retrieveUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Retrieve response status:', retrieveResponse.statusCode);

    if (retrieveResponse.statusCode !== 200) {
      console.error('Failed to retrieve job details:', retrieveResponse.statusCode);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job still processing...'
      });
    }

    let jobDetails;
    try {
      jobDetails = JSON.parse(retrieveResponse.body);
    } catch (e) {
      console.error('Failed to parse job details', e);
      return res.status(502).json({
        success: false,
        error: 'Invalid job detail response from SwarmNode'
      });
    }

    console.log('Job details summary:', {
      id: jobDetails.id,
      status: jobDetails.status,
      has_output: !!jobDetails.output,
      has_result: !!jobDetails.result,
      has_return_value: !!jobDetails.return_value
    });

    const isCompleted =
      jobDetails.status === 'completed' || jobDetails.status === 'success';

    // The optimizer's main() returns the lineup dict → SwarmNode stores it here:
    const returnValue =
      jobDetails.return_value || jobDetails.output || jobDetails.result || {};
    const hasLineup = returnValue.lineup && Array.isArray(returnValue.lineup);

    console.log('Return value has lineup:', hasLineup);

    if (isCompleted && hasLineup) {
      console.log(`✅ Completed lineup found for job ${jobDetails.id}`);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: returnValue.lineup || [],
        stats: returnValue.stats || {},
        locked_player_used: returnValue.locked_player_used || null,
        lineup_export: returnValue.lineup_export || null,
        recommendations: returnValue.recommendations || [],
        job_id: jobDetails.id,
        created_at: jobDetails.created_at
      });
    }

    // Still running?
    if (
      jobDetails.status === 'running' ||
      jobDetails.status === 'pending' ||
      jobDetails.status === 'queued'
    ) {
      console.log('Job still processing...');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    // Failed?
    if (jobDetails.status === 'failed' || jobDetails.status === 'error') {
      console.error('Job failed:', jobDetails.error);
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: jobDetails.error || 'Optimization failed'
      });
    }

    // Completed but no lineup yet – rare edge case
    console.log('Job completed but no lineup on return_value yet');
    return res.status(200).json({
      success: true,
      status: 'processing',
      message: 'Waiting for lineup data...',
      debug_status: jobDetails.status
    });

  } catch (error) {
    console.error('❌ /api/results error:', error.message);

    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
