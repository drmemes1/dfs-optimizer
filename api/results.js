// api/results.js - Get latest OPTIMIZER result (no ingest involved)
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
    const SWARMNODE_BASE =
      (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/+$/, '');
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID;

    if (!SWARMNODE_KEY || !OPTIMIZER_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY or OPTIMIZER_AGENT_ID not configured'
      });
    }

    const triggerJobId = req.query.job_id; // just for logging (ingest job), not used in API
    console.log(`\n🔍 Checking latest OPTIMIZER job (optimizer only). Triggered by job: ${triggerJobId || 'N/A'}`);

    // -----------------------------------------------------------------------
    // STEP 1: List latest executor jobs for this OPTIMIZER agent
    // Endpoint: /v1/agent-executor-jobs/?agent_id=...&ordering=-created_at&limit=1
    // -----------------------------------------------------------------------
    const listUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/?agent_id=${OPTIMIZER_AGENT_ID}&ordering=-created_at&limit=1`;
    console.log('Step 1: Listing optimizer jobs from:', listUrl);

    const listResp = await makeRequest(listUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('List response status:', listResp.statusCode);

    if (listResp.statusCode !== 200) {
      console.error('Failed to list optimizer jobs:', listResp.statusCode, listResp.body);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResp.body);
    } catch (e) {
      console.error('Failed to parse job list JSON:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode when listing jobs'
      });
    }

    const jobs = listData.results || listData.jobs || [];
    console.log(`Found ${jobs.length} optimizer job(s)`);

    if (jobs.length === 0) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'No optimizer jobs found yet'
      });
    }

    const latestJob = jobs[0];
    console.log('Latest optimizer job summary:', latestJob);

    // If SwarmNode already put return_value on the list item, use it directly
    if (latestJob.return_value) {
      console.log('✅ Return value is already present on latestJob');
      const rv = latestJob.return_value || {};
      const lineup = rv.lineup || [];
      const stats = rv.stats || {};
      const recommendations = rv.recommendations || [];

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup,
        stats,
        locked_player_used: rv.locked_player_used || null,
        lineup_export: rv.lineup_export || null,
        recommendations,
        job_id: latestJob.id,
        created_at: latestJob.created_at
      });
    }

    // -----------------------------------------------------------------------
    // STEP 2: Fetch full job details (to access return_value)
    // Endpoint: /v1/agent-executor-jobs/{job_id}/
    // -----------------------------------------------------------------------
    const jobId = latestJob.id;
    const detailUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
    console.log('Step 2: Retrieving executor job details from:', detailUrl);

    const detailResp = await makeRequest(detailUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Executor job response status:', detailResp.statusCode);

    if (detailResp.statusCode !== 200) {
      console.error('Failed to retrieve executor job:', detailResp.statusCode, detailResp.body);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    let jobDetail;
    try {
      jobDetail = JSON.parse(detailResp.body);
    } catch (e) {
      console.error('Failed to parse executor job JSON:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode when reading job details'
      });
    }

    console.log('Executor job summary:', {
      id: jobDetail.id,
      status: jobDetail.status,
      has_output: jobDetail.has_output,
      has_result: jobDetail.has_result,
      has_return_value: jobDetail.has_return_value
    });

    const status = jobDetail.status || latestJob.status || 'unknown';

    // If job is not finished yet, tell the frontend to keep polling
    if (
      status === 'queued' ||
      status === 'pending' ||
      status === 'running' ||
      status === 'unknown'
    ) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    // If job failed
    if (status === 'failed' || status === 'error') {
      console.error('Optimizer job failed:', jobDetail.error || jobDetail);
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: jobDetail.error || 'Optimization failed'
      });
    }

    // -----------------------------------------------------------------------
    // STEP 3: Extract the optimizer return_value and shape it for the UI
    // -----------------------------------------------------------------------
    const rv =
      jobDetail.return_value ||
      (jobDetail.output && jobDetail.output.return_value) ||
      jobDetail.output ||
      {};

    const lineup = rv.lineup || [];
    const stats = rv.stats || {};
    const recommendations = rv.recommendations || [];

    if (!Array.isArray(lineup) || lineup.length === 0) {
      console.log('Job completed but no lineup found yet');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job completed but no lineup available yet'
      });
    }

    console.log(`✅ Found lineup for job ${jobId} with ${lineup.length} players`);

    return res.status(200).json({
      success: true,
      status: 'completed',
      lineup,
      stats,
      locked_player_used: rv.locked_player_used || null,
      lineup_export: rv.lineup_export || null,
      recommendations,
      job_id: jobId,
      created_at: jobDetail.created_at || latestJob.created_at
    });
  } catch (error) {
    console.error('❌ /api/results error:', error.message);
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
