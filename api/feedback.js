// api/feedback.js
const https = require('https');

function makeRequest(url, options, body) {
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
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => {
        resolve({ statusCode: res.statusCode, body: data });
      });
    });

    req.on('error', reject);

    if (body) {
      req.write(body);
    }
    req.end();
  });
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
  const SWARMNODE_BASE = (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/+$/, '');
  const LEARNER_AGENT_ID = process.env.LEARNER_AGENT_ID; // <-- set this in Vercel

  if (!SWARMNODE_KEY || !LEARNER_AGENT_ID) {
    return res.status(500).json({
      ok: false,
      error: 'Missing SWARMNODE_API_KEY or LEARNER_AGENT_ID'
    });
  }

  try {
    const body = await new Promise((resolve, reject) => {
      let data = '';
      req.on('data', chunk => { data += chunk; });
      req.on('end', () => resolve(data));
      req.on('error', reject);
    });

    let payload;
    try {
      payload = JSON.parse(body || '{}');
    } catch (e) {
      return res.status(400).json({ ok: false, error: 'Invalid JSON body' });
    }

    const { job_id, lineup, winning_total, winning_lineup } = payload || {};

    if (!job_id || !Array.isArray(lineup) || lineup.length === 0) {
      return res.status(400).json({
        ok: false,
        error: 'Missing job_id or lineup in request body'
      });
    }

    // Normalize lineup payload to send to LEARNER
    const learningInput = lineup.map(p => ({
      slot: p.slot,
      name: p.name,
      team: p.team || null,
      salary: p.salary_num || p.salary || null,
      projection: typeof p.projection === 'number' ? p.projection : null,
      actual_fp: typeof p.actual_fp === 'number' ? p.actual_fp : null,
      is_locked: !!p.is_locked
    }));

    const learnerPayload = {
      optimizer_job_id: job_id,
      used_lineup: learningInput,
      winning_total: typeof winning_total === 'number' ? winning_total : null,
      winning_lineup: Array.isArray(winning_lineup) ? winning_lineup : null,
      submitted_at: new Date().toISOString()
    };

    // STEP 1: Create a learner job
    const createUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const createBody = JSON.stringify({
      agent_id: LEARNER_AGENT_ID,
      payload: learnerPayload
    });

    console.log('📡 Creating LEARNER job at:', createUrl);

    const createResp = await makeRequest(createUrl, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    }, createBody);

    if (![200, 201].includes(createResp.statusCode)) {
      return res.status(502).json({
        ok: false,
        error: `Failed to create learner job: ${createResp.statusCode}`,
        body: createResp.body
      });
    }

    let created;
    try {
      created = JSON.parse(createResp.body);
    } catch (e) {
      return res.status(502).json({
        ok: false,
        error: 'Invalid learner create JSON',
        body: createResp.body
      });
    }

    const learnerJobId = created.id || created.execution_address || created.job_id;
    console.log('🧠 LEARNER job created:', learnerJobId);

    // STEP 2: Poll the learner job a few times for return_value
    const jobUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${learnerJobId}/`;
    let jobDetail = null;
    const maxPolls = 10;

    for (let i = 0; i < maxPolls; i++) {
      await sleep(1000);

      const jobResp = await makeRequest(jobUrl, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${SWARMNODE_KEY}`,
          'Content-Type': 'application/json'
        }
      });

      if (jobResp.statusCode !== 200) continue;

      try {
        jobDetail = JSON.parse(jobResp.body);
      } catch (e) {
        continue;
      }

      if (jobDetail.status === 'completed' || jobDetail.status === 'success') {
        break;
      }
    }

    if (!jobDetail || !(jobDetail.status === 'completed' || jobDetail.status === 'success')) {
      // Didn’t finish in time – still useful to know ID
      return res.status(200).json({
        ok: true,
        status: 'processing',
        learner_job_id: learnerJobId,
        message: 'Learner job started but not finished yet. Check SwarmNode UI for full details.'
      });
    }

    const rv = jobDetail.return_value || jobDetail.output || jobDetail.result || {};

    return res.status(200).json({
      ok: true,
      status: jobDetail.status,
      learner_job_id: learnerJobId,
      learner_return_value: rv
    });

  } catch (error) {
    console.error('❌ feedback error:', error);
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
