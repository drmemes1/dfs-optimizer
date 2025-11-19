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
    const SWARMNODE_BASE = (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/$/, '');
    const OPTIMIZER_AGENT_ID = (process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3').trim();

    const ingestJobId = req.query.job_id; // just for logging/debug

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    console.log(`\n🔍 Checking latest OPTIMIZER job (triggered by job: ${ingestJobId})`);

    // ------------------------------------------------------------------
    // STEP 1: list latest agent-executor jobs for this optimizer agent
    // ------------------------------------------------------------------
    // This hits the global agent-executor jobs endpoint and filters by agent_id.
    const listUrl =
      `${SWARMNODE_BASE}/v1/agent-executor-jobs/?` +
      `agent_id=${encodeURIComponent(OPTIMIZER_AGENT_ID)}` +
      `&ordering=-created_at&limit=5`;

    console.log('Step 1: Listing optimizer jobs from:', listUrl);

    const listResponse = await makeRequest(listUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('List response status:', listResponse.statusCode);

    if (listResponse.statusCode !== 200) {
      console.error('Failed to list optimizer jobs:', listResponse.statusCode, listResponse.body);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to complete...'
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResponse.body);
    } catch (e) {
      console.error('Failed to parse optimizer jobs list:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (jobs list)'
      });
    }

    // SwarmNode usually wraps results in "results"
    const jobs = listData.results || listData.jobs || [];
    console.log(`Found ${jobs.length} optimizer job(s)`);

    if (!jobs.length) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    // Take the newest job (first because of ordering=-created_at)
    const latestJob = jobs[0];
    console.log(
      `Latest OPTIMIZER job: ${latestJob.id}, status=${latestJob.status}, agent_id=${latestJob.agent_id}`
    );

    // ------------------------------------------------------------------
    // STEP 2: fetch full job details (to get return_value)
    // ------------------------------------------------------------------
    const retrieveUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latestJob.id}/`;

    console.log('Step 2: Retrieving full job details from:', retrieveUrl);

    const retrieveResponse = await makeRequest(retrieveUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Retrieve response status:', retrieveResponse.statusCode);

    if (retrieveResponse.statusCode !== 200) {
      console.error('Failed to retrieve optimizer job:', retrieveResponse.statusCode, retrieveResponse.body);
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
      console.error('Failed to parse optimizer job details:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid job response from SwarmNode'
      });
    }

    console.log('Job details summary:', {
      id: jobDetails.id,
      status: jobDetails.status,
      has_output: !!jobDetails.output,
      has_result: !!jobDetails.result,
      has_return_value: !!jobDetails.return_value
    });

    const status = jobDetails.status;
    const isCompleted = status === 'completed' || status === 'success';

    // SwarmNode puts your Python main() return here:
    const retval =
      jobDetails.return_value || jobDetails.output || jobDetails.result || {};

    const hasLineup = retval.lineup && Array.isArray(retval.lineup);
    console.log('Return value has lineup:', hasLineup ? `yes (${retval.lineup.length})` : 'no');

    if (isCompleted && hasLineup) {
      console.log(`✅ Completed lineup found for job ${jobDetails.id}`);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: retval.lineup || [],
        stats: retval.stats || {},
        locked_player_used: retval.locked_player_used || null,
        lineup_export: retval.lineup_export || null,
        recommendations: retval.recommendations || [],
        job_id: jobDetails.id,
        created_at: jobDetails.created_at
      });
    }

    if (status === 'running' || status === 'pending' || status === 'queued') {
      console.log('Job still processing...');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    if (status === 'failed' || status === 'error') {
      console.error('Optimizer job failed:', jobDetails.error);
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: jobDetails.error || 'Optimization failed'
      });
    }

    console.log('Job completed but no lineup present yet, status=', status);
    return res.status(200).json({
      success: true,
      status: 'processing',
      message: 'Waiting for lineup data...',
      debug_status: status
    });

  } catch (error) {
    console.error('❌ /api/results error:', error.message);

    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
