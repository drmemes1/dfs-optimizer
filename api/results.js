// api/results.js - Get OPTIMIZER lineup result for a specific job_id
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

    console.log(`\n🔍 Checking results for executor job: ${executorJobId}`);

    // -------------------------------------------------------
    // STEP 1: Get the EXECUTOR job (the one created by ingest)
    // -------------------------------------------------------
    const execUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${executorJobId}/`;
    console.log('Step 1: Retrieving executor job details from:', execUrl);

    const execResp = await makeRequest(execUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Executor job response status:', execResp.statusCode);

    if (execResp.statusCode !== 200) {
      // If SwarmNode hasn’t created the executor job yet, treat as processing
      console.log('Raw executor job response:', execResp.body);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    let execJob;
    try {
      execJob = JSON.parse(execResp.body);
    } catch (e) {
      console.error('Failed to parse executor job JSON:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (executor job)'
      });
    }

    const execStatus = execJob.status;
    const execAddress = execJob.execution_address;

    console.log('Executor job summary:', {
      id: execJob.id,
      status: execStatus,
      execution_address: execAddress,
      has_output: !!execJob.output,
      has_result: !!execJob.result,
      has_return_value: !!(execJob.return_value)
    });

    // If executor still running / queued, just say "processing"
    if (['pending', 'running', 'queued', 'unknown'].includes(execStatus)) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    // If executor finished but we have a direct return_value on it,
    // just pass that straight through (nice simple case)
    if (execJob.return_value) {
      console.log('✅ Executor job has direct return_value; forwarding it.');
      const rv = execJob.return_value;

      return res.status(200).json({
        success: true,
        status: 'completed',
        // If return_value already looks like your lineup object, just pass it:
        ...rv
      });
    }

    // -------------------------------------------------------
    // STEP 2: Find the INNER AGENT JOB using execution_address
    // -------------------------------------------------------
    if (!execAddress) {
      console.log('Executor job has no execution_address, nothing to drill into.');
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: 'Executor job completed but no execution_address / return_value found'
      });
    }

    // NOTE: endpoint name here is based on SwarmNode’s API pattern:
    // /v1/agent-jobs/?execution_address=...
    const innerUrl = `${SWARMNODE_BASE}/v1/agent-jobs/?execution_address=${encodeURIComponent(execAddress)}`;
    console.log('Step 2: Retrieving inner agent job from:', innerUrl);

    const innerResp = await makeRequest(innerUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Inner job response status:', innerResp.statusCode);

    if (innerResp.statusCode !== 200) {
      console.log('Raw inner job response:', innerResp.body);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Inner agent job not ready yet...'
      });
    }

    let innerData;
    try {
      innerData = JSON.parse(innerResp.body);
    } catch (e) {
      console.error('Failed to parse inner job JSON:', e.message);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode (inner job)'
      });
    }

    const innerJobs = innerData.results || innerData.jobs || [];
    console.log(`Found ${innerJobs.length} inner job(s)`);

    if (innerJobs.length === 0) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Inner agent job not found yet...'
      });
    }

    const innerJob = innerJobs[0];
    console.log('Inner job summary:', {
      id: innerJob.id,
      status: innerJob.status,
      has_output: !!innerJob.output
    });

    // If inner job is still running
    if (['pending', 'running', 'queued', 'unknown'].includes(innerJob.status)) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimizer still running...'
      });
    }

    // -------------------------------------------------------
    // STEP 3: Extract the OPTIMIZER return value and forward it
    // -------------------------------------------------------
    const output = innerJob.output || {};
    const returnValue = output.return_value || output.returnValue || null;

    if (!returnValue) {
      console.log('Inner job has no return_value field:', JSON.stringify(output).slice(0, 300));
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: 'Job completed but no lineup found in return_value'
      });
    }

    console.log('✅ Found return_value from OPTIMIZER, forwarding to client.');

    // If your OPTIMIZER returns exactly the lineup object you showed earlier,
    // this just passes it straight through.
    return res.status(200).json({
      success: true,
      status: 'completed',
      ...returnValue
    });

  } catch (error) {
    console.error('❌ /api/results error:', error.message);
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
