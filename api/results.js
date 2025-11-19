// api/results.js - Get latest OPTIMIZER result for a given executor job
const https = require('https');

function makeRequest(url, options = {}) {
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
    const SWARMNODE_BASE = (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/+$/, '');
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID; // 👈 make sure this matches Vercel env name

    const executorJobId = req.query.job_id;

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    if (!executorJobId) {
      return res.status(400).json({
        success: false,
        error: 'Missing job_id query parameter'
      });
    }

    if (!OPTIMIZER_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: 'OPTIMIZER_AGENT_ID not configured'
      });
    }

    console.log(`\n🔍 Checking OPTIMIZER result for executor job: ${executorJobId}`);

    // -------------------------------------------------------------------
    // STEP 1: Get the executor job (INGEST pipeline job)
    // -------------------------------------------------------------------
    const execUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${executorJobId}/`;
    console.log('Step 1: Retrieving executor job from:', execUrl);

    const execResp = await makeRequest(execUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      }
    });

    console.log('Executor job response status:', execResp.statusCode);

    if (execResp.statusCode !== 200) {
      console.error('Executor job raw status=', execResp.statusCode, 'body=', execResp.body);
      // Treat as "still processing" so the UI keeps polling
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    let executorJob;
    try {
      executorJob = JSON.parse(execResp.body);
    } catch (e) {
      console.error('Failed to parse executor job JSON:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (executor job)'
      });
    }

    console.log('Executor job summary:', {
      id: executorJob.id,
      status: executorJob.status,
      execution_address: executorJob.execution_address,
      has_output: executorJob.has_output,
      has_result: executorJob.has_result,
      has_return_value: executorJob.has_return_value
    });

    // If executor job is still running, tell frontend to keep waiting
    if (
      !executorJob.execution_address &&
      (executorJob.status === 'pending' ||
       executorJob.status === 'running' ||
       executorJob.status === 'queued' ||
       executorJob.status === 'unknown')
    ) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress (executor job)...'
      });
    }

    const innerJobId = executorJob.execution_address;

    if (!innerJobId) {
      // Executor finished but never spawned optimizer job (or failed)
      console.log('Executor job completed but no execution_address / inner job id yet');
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: 'Executor job completed but no optimizer job found'
      });
    }

    // -------------------------------------------------------------------
    // STEP 2: Get the inner OPTIMIZER agent job by id
    // -------------------------------------------------------------------
    const innerUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/${innerJobId}/`;
    console.log('Step 2: Retrieving OPTIMIZER agent job from:', innerUrl);

    const innerResp = await makeRequest(innerUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Accept': 'application/json'
      }
    });

    console.log('Inner (optimizer) job response status:', innerResp.statusCode);

    if (innerResp.statusCode !== 200) {
      console.error('Failed to retrieve inner agent job:', innerResp.body);
      // Again, treat as still processing rather than hard fail
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress (optimizer job)...'
      });
    }

    let innerJob;
    try {
      innerJob = JSON.parse(innerResp.body);
    } catch (e) {
      console.error('Failed to parse inner job JSON:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (optimizer job)'
      });
    }

    console.log('Optimizer job summary:', {
      id: innerJob.id,
      status: innerJob.status,
      has_output: innerJob.has_output,
      has_result: innerJob.has_result,
      has_return_value: innerJob.has_return_value
    });

    const innerStatus = innerJob.status || 'unknown';

    if (['pending', 'running', 'queued', 'unknown'].includes(innerStatus)) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress (optimizer job running)...'
      });
    }

    if (['failed', 'error', 'cancelled'].includes(innerStatus)) {
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: innerJob.error || 'Optimizer job failed'
      });
    }

    // -------------------------------------------------------------------
    // STEP 3: Extract the return_value (this is what .execute().return_value gives)
    // -------------------------------------------------------------------
    const rv =
      innerJob.return_value ||
      (innerJob.output && innerJob.output.return_value) ||
      innerJob.output ||
      null;

    if (!rv) {
      console.log('Job completed but no return_value found on inner job');
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: 'No optimizer return value found'
      });
    }

    // If the optimizer main() already returns the "ok/lineup/stats" object,
    // just forward it to the frontend.
    const lineup = rv.lineup || [];
    const stats = rv.stats || {};
    const recommendations = rv.recommendations || [];
    const lockedPlayerUsed = rv.locked_player_used || null;

    if (lineup.length > 0) {
      console.log(`✅ Found lineup with ${lineup.length} players from optimizer job ${innerJob.id}`);
      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup,
        stats,
        recommendations,
        locked_player_used: lockedPlayerUsed,
        lineup_export: rv.lineup_export || null,
        job_id: innerJob.id
      });
    }

    // Fallback: return raw return_value so you can inspect it in the browser
    console.log('Optimizer return_value did not contain lineup; returning raw payload');
    return res.status(200).json({
      success: true,
      status: 'completed',
      raw: rv,
      job_id: innerJob.id
    });

  } catch (error) {
    console.error('❌ /api/results error:', error);
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
