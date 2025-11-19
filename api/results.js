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
      res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
    });

    req.on('error', reject);
    req.end();
  });
}

// helper to dig out a return_value no matter where they hid it 🙂
function extractReturnValue(obj) {
  if (!obj || typeof obj !== 'object') return null;

  // Most likely places
  if (obj.return_value) return obj.return_value;
  if (obj.output && obj.output.return_value) return obj.output.return_value;
  if (obj.result && obj.result.return_value) return obj.result.return_value;
  if (obj.result && !obj.return_value) return obj.result;
  if (obj.output && !obj.return_value) return obj.output;

  // Sometimes nested under latest_execution
  if (obj.latest_execution && obj.latest_execution.return_value) {
    return obj.latest_execution.return_value;
  }

  // Or execution.return_value
  if (obj.execution && obj.execution.return_value) {
    return obj.execution.return_value;
  }

  // Last resort: if this already *looks* like the dict we expect (has lineup)
  if (obj.lineup && Array.isArray(obj.lineup)) return obj;

  return null;
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

    // STEP 1: list agent-executor jobs for this optimizer agent
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

    // STEP 2: get executor job details – this gives us execution_address
    const execJobUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latestJobId}/`;
    console.log('Step 2: Retrieving executor job details from:', execJobUrl);

    const execJobResponse = await makeRequest(execJobUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Executor job response status:', execJobResponse.statusCode);

    if (execJobResponse.statusCode !== 200) {
      console.error('Failed to retrieve executor job:', execJobResponse.statusCode, execJobResponse.body);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job still processing...'
      });
    }

    let execJob;
    try {
      execJob = JSON.parse(execJobResponse.body);
    } catch (e) {
      console.error('Failed to parse executor job:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid executor job response from SwarmNode'
      });
    }

    const execStatus =
      execJob.status ||
      execJob.execution_status ||
      execJob.state ||
      'unknown';

    const innerJobId =
      execJob.execution_address ||
      execJob.inner_job_id ||
      execJob.agent_job_id;

    console.log('Executor job summary:', {
      id: execJob.id,
      status: execStatus,
      execution_address: innerJobId,
      has_output: !!execJob.output,
      has_result: !!execJob.result,
      has_return_value: !!execJob.return_value
    });

    // If somehow the executor job already has a return_value, try that first
    let returnValue = extractReturnValue(execJob);
    let jobStatus = execStatus;
    let jobDetails = execJob;

    // STEP 3: follow execution_address to /v1/agent-jobs/ (inner job)
    if (!returnValue && innerJobId) {
      const innerUrl = `${SWARMNODE_BASE}/v1/agent-jobs/${innerJobId}/`;
      console.log('Step 3: Retrieving inner agent job from:', innerUrl);

      const innerResponse = await makeRequest(innerUrl, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${SWARMNODE_KEY}`,
          'Content-Type': 'application/json'
        }
      });

      console.log('Inner agent job response status:', innerResponse.statusCode);

      if (innerResponse.statusCode !== 200) {
        console.error('Failed to retrieve inner agent job:', innerResponse.statusCode, innerResponse.body);
        return res.status(200).json({
          success: true,
          status: 'processing',
          message: 'Optimization in progress...'
        });
      }

      try {
        jobDetails = JSON.parse(innerResponse.body);
      } catch (e) {
        console.error('Failed to parse inner agent job:', e.message);
        console.log('Inner raw body:', innerResponse.body); // 👈 log entire JSON
        return res.status(502).json({
          success: false,
          error: 'Invalid inner job response from SwarmNode'
        });
      }

      console.log('Inner job raw body:', JSON.stringify(jobDetails, null, 2)); // 👈 full dump

      jobStatus =
        jobDetails.status ||
        jobDetails.execution_status ||
        jobDetails.state ||
        jobStatus;

      returnValue = extractReturnValue(jobDetails);

      console.log('Inner job summary:', {
        id: jobDetails.id,
        status: jobStatus,
        has_output: !!jobDetails.output,
        has_result: !!jobDetails.result,
        has_return_value: !!(returnValue)
      });
    }

    // STEP 4: interpret status + return_value
    const rv = returnValue || {};
    const lineup = rv.lineup;
    const hasLineup = Array.isArray(lineup) && lineup.length > 0;

    const isCompleted =
      jobStatus === 'completed' ||
      jobStatus === 'success' ||
      jobStatus === 'succeeded';

    if (isCompleted && hasLineup) {
      console.log(`✅ Found completed lineup! Job ID: ${jobDetails.id}`);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup,
        stats: rv.stats || {},
        locked_player_used: rv.locked_player_used || null,
        lineup_export: rv.lineup_export || null,
        recommendations: rv.recommendations || [],
        job_id: jobDetails.id,
        created_at: jobDetails.created_at
      });
    }

    if (['running', 'pending', 'queued'].includes(jobStatus)) {
      console.log('Job still processing...', jobStatus);
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
