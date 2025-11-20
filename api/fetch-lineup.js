// api/fetch-lineup.js
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
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'GET') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
  const SWARMNODE_BASE = (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/+$/, '');

  if (!SWARMNODE_KEY) {
    return res.status(500).json({
      ok: false,
      error: 'SWARMNODE_API_KEY not configured'
    });
  }

  const jobId = req.query.job_id;
  if (!jobId) {
    return res.status(400).json({
      ok: false,
      error: 'Missing job_id query parameter'
    });
  }

  try {
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
    console.log('🔍 Fetching OPTIMIZER job details from:', url);

    const response = await makeRequest(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('Job detail status:', response.statusCode);

    if (response.statusCode !== 200) {
      return res.status(502).json({
        ok: false,
        error: `Failed to fetch job: status ${response.statusCode}`,
        raw: response.body
      });
    }

    let job;
    try {
      job = JSON.parse(response.body);
    } catch (e) {
      console.error('Failed to parse job response:', e.message);
      return res.status(502).json({
        ok: false,
        error: 'Invalid job JSON from SwarmNode'
      });
    }

    const status = job.status;
    const rv = job.return_value || job.output || job.result || {};
    const lineup = rv.lineup || [];
    const stats = rv.stats || {};
    const recommendations = rv.recommendations || [];

    const isCompleted = status === 'completed' || status === 'success';

    if (!isCompleted) {
      return res.status(200).json({
        ok: true,
        status,
        message: 'Job still processing',
        job_id: job.id
      });
    }

    if (!Array.isArray(lineup) || lineup.length === 0) {
      return res.status(200).json({
        ok: false,
        status,
        error: 'No lineup found in return_value',
        job_id: job.id,
        raw_return_value: rv
      });
    }

    // Normalize salary to number as well (for learner math later)
    const lineupNormalized = lineup.map(p => {
      let salaryNum = null;
      if (typeof p.salary === 'string') {
        salaryNum = parseFloat(p.salary.replace(/[^0-9.]/g, '')) || null;
      } else if (typeof p.salary === 'number') {
        salaryNum = p.salary;
      }

      return {
        slot: p.slot,
        name: p.name,
        team: p.team || null,
        salary: p.salary,
        salary_num: salaryNum,
        projection: p.projection,
        value: p.value,
        is_locked: !!p.is_locked
      };
    });

    return res.status(200).json({
      ok: true,
      status,
      job_id: job.id,
      created_at: job.created_at,
      slate_type: rv.slate_type || null,
      lineup: lineupNormalized,
      stats,
      recommendations
    });

  } catch (error) {
    console.error('❌ fetch-lineup error:', error);
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
