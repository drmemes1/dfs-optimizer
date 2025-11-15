// api/tracker.js
const https = require('https');

function makeRequest(url, options, postData) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname,
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
    if (postData) req.write(postData);
    req.end();
  });
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const TRACKER_AGENT_ID = process.env.TRACKER_AGENT_ID;

    if (!SWARMNODE_KEY || !TRACKER_AGENT_ID) {
      return res.status(500).json({
        ok: false,
        error: 'Missing TRACKER_AGENT_ID in Vercel env variables'
      });
    }

    const jobId = req.body?.job_id || '';
    const slateDate = req.body?.slate_date || new Date().toISOString().split('T')[0];

    if (!jobId) {
      return res.status(400).json({
        ok: false,
        error: 'Missing job_id',
        details: 'Provide the Optimizer job ID'
      });
    }

    console.log(`📊 TRACKER: Fetching optimizer results from job ${jobId}`);

    // Fetch the optimizer job results
    const jobUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
    const jobResponse = await makeRequest(jobUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`
      }
    });

    if (jobResponse.statusCode !== 200) {
      return res.status(404).json({
        ok: false,
        error: 'Could not fetch optimizer job',
        job_id: jobId
      });
    }

    const jobData = JSON.parse(jobResponse.body);
    const optimizerOutput = jobData.output || {};

    console.log(`✅ Fetched optimizer output`);

    // Call TRACKER agent with optimizer output + slate date
    const payload = {
      optimizer_output: optimizerOutput,
      slate_date: slateDate,
      optimizer_job_id: jobId
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify({
      agent_id: TRACKER_AGENT_ID,
      payload: payload
    });

    const response = await makeRequest(url, {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json',
        'Content-Length': Buffer.byteLength(postData)
      }
    }, postData);

    let result;
    try {
      result = JSON.parse(response.body);
    } catch {
      result = { raw: response.body };
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      return res.status(200).json({
        success: true,
        message: `📈 TRACKER analyzing ${slateDate} results`,
        tracker_job_id: result.job_id || result.id,
        optimizer_job_id: jobId,
        swarmnode_link: 'https://app.swarmnode.ai'
      });
    } else {
      return res.status(response.statusCode || 500).json({
        ok: false,
        error: 'TRACKER agent failed',
        details: result
      });
    }

  } catch (error) {
    console.error('❌ TRACKER error:', error);
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
