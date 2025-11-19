// api/results.js - Retrieve specific job for return value
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
    const SWARMNODE_BASE = (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/+$/, '');
    const OPTIMIZER_AGENT_ID = (process.env.OPTIMIZER_AGENT_ID || '').trim();

    const ingestJobId = req.query.job_id;

    if (!SWARMNODE_KEY || !OPTIMIZER_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY or OPTIMIZER_AGENT_ID not configured'
      });
    }

    console.log(`\n🔍 Checking latest OPTIMIZER job (triggered by job: ${ingestJobId})`);

    // STEP 1: list optimizer jobs for this agent
    const listUrl =
      `${SWARMNODE_BASE}/v1/agent-executor-jobs/` +
      `?agent_id=${encodeURIComponent(OPTIMIZER_AGENT_ID)}` +
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
      console.error('Failed to parse list response:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (list)'
      });
    }

    // SwarmNode may return {results: [...]} or just [...]
    const jobs = Array.isArray(listData)
      ? listData
      : (listData.results || listData.jobs || []);

    console.log(`Found ${jobs.length} optimizer job(s)`);

    if (!jobs.length) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    const latestJob = jobs[0];
    const latestJobId = latestJob.id || latestJob.job_id || latestJob.execution_address;

    console.log(
      `Latest OPTIMIZER job: ${latestJobId}, ` +
      `raw status=${latestJob.status || latestJob.execution_status}`
    );

    if (!latestJobId) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Latest job has no ID yet'
      });
    }

    // STEP 2: retrieve full job details to get return_value
    const retrieveUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latestJobId}/`;
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
      console.error('Failed to retrieve job:', retrieveResponse.statusCode, retrieveResponse.body);
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
      console.error('Failed to parse job details:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid job response from SwarmNode'
      });
    }

    const jobStatus =
      jobDetails.status ||
      jobDetails.execution_status ||
      jobDetails.state ||
      'unknown';

    console.log('Job details summary:', {
      id: jobDetails.id,
      status: jobStatus,
      has_output: !!jobDetails.output,
      has_result: !!jobDetails.result,
      has_return_value: !!jobDetails.return_value
    });

    // The optimizer's main() return becomes return_value
    const returnValue =
      jobDetails.return_value ||
      jobDetails.output ||
      jobDetails.result ||
      {};

    const lineup = returnValue.lineup;
    const hasLineup = Array.isArray(lineup) && lineup.length > 0;

    const isCompleted =
      jobStatus === 'completed' ||
      jobStatus === 'success';

    if (isCompleted && hasLineup) {
      console.log(`✅ Found completed lineup! Job ID: ${jobDetails.id}`);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup,
        stats: returnValue.stats || {},
        locked_player_used: returnValue.locked_player_used || null,
        lineup_export: returnValue.lineup_export || null,
        recommendations: returnValue.recommendations || [],
        job_id: jobDetails.id,
        created_at: jobDetails.created_at
      });
    }

    if (['running', 'pending', 'queued'].includes(jobStatus)) {
      console.log('Job still processing...');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    if (['failed', 'error'].includes(jobStatus)) {
      console.error('Job failed:', jobDetails.error);
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: jobDetails.error || 'Optimization failed'
      });
    }

    console.log('Job completed but no lineup found yet');
    return res.status(200).json({
      success: true,
      status: 'processing',
      message: 'Waiting for lineup data...',
      debug_status: jobStatus
    });

  } catch (error) {
    console.error('❌ /api/results error:', error.message);
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
