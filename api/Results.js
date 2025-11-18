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
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3';

    const ingestJobId = req.query.job_id; // just for logging

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    console.log(`\n🔍 Checking OPTIMIZER result (ingest job: ${ingestJobId || 'n/a'})`);

    // ─────────────────────────────────────────
    // STEP 1: Get latest OPTIMIZER job ID
    // ─────────────────────────────────────────
    const listUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/?limit=1&ordering=-created_at`;
    console.log('Step 1: Listing jobs from:', listUrl);

    const listResp = await makeRequest(listUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('List response status:', listResp.statusCode);

    if (listResp.statusCode !== 200) {
      console.error('Failed to list optimizer jobs:', listResp.statusCode);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to complete...'
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResp.body);
    } catch (e) {
      console.error('Failed to parse list response', e);
      return res.status(502).json({
        success: false,
        error: 'Invalid job list response from SwarmNode'
      });
    }

    const jobs = listData.results || listData.jobs || [];
    console.log(`Found ${jobs.length} optimizer job(s)`);

    if (!jobs.length) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Waiting for optimization to start...'
      });
    }

    const latestJobId = jobs[0].id;
    console.log(`Latest OPTIMIZER job: ${latestJobId}, status=${jobs[0].status}`);

    // ─────────────────────────────────────────
    // STEP 2: Fetch full job details (for return_value)
    // ─────────────────────────────────────────
    const jobUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latestJobId}/`;
    console.log('Step 2: Fetching job details from:', jobUrl);

    const jobResp = await makeRequest(jobUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Job details status:', jobResp.statusCode);

    if (jobResp.statusCode !== 200) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job still processing...'
      });
    }

    let job;
    try {
      job = JSON.parse(jobResp.body);
    } catch (e) {
      console.error('Failed to parse job details', e);
      return res.status(502).json({
        success: false,
        error: 'Invalid job detail response from SwarmNode'
      });
    }

    console.log('Job detail summary:', {
      id: job.id,
      status: job.status,
      has_return_value: !!job.return_value,
      has_output: !!job.output,
      has_result: !!job.result
    });

    const isCompleted =
      job.status === 'completed' || job.status === 'success';

    // Prefer return_value, but fall back if needed
    const rv = job.return_value || job.output || job.result || {};
    const lineup = rv.lineup || [];
    const hasLineup = Array.isArray(lineup) && lineup.length > 0;

    if (isCompleted && hasLineup) {
      console.log(`✅ Completed lineup found for job ${job.id} (players: ${lineup.length})`);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup,
        stats: rv.stats || {},
        locked_player_used: rv.locked_player_used || null,
        lineup_export: rv.lineup_export || null,
        recommendations: rv.recommendations || [],
        job_id: job.id,
        created_at: job.created_at
      });
    }

    // Still running
    if (
      job.status === 'pending' ||
      job.status === 'queued' ||
      job.status === 'running'
    ) {
      console.log('Job still processing on SwarmNode…');
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Optimization in progress...'
      });
    }

    // Completed but no lineup (fallback)
    console.log('Job finished but no lineup in return_value yet');
    return res.status(200).json({
      success: true,
      status: 'completed_no_lineup',
      message: 'Job finished but no lineup was returned',
      debug: {
        job_status: job.status,
        keys: Object.keys(rv || {})
      }
    });

  } catch (err) {
    console.error('❌ /api/results error:', err);
    return res.status(500).json({
      success: false,
      error: err.message
    });
  }
};
