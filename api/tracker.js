// api/tracker.js
const https = require('https');

function parseBody(req) {
  return new Promise((resolve, reject) => {
    let body = '';
    req.on('data', chunk => { body += chunk; });
    req.on('end', () => {
      if (!body) return resolve({});
      try {
        resolve(JSON.parse(body));
      } catch (e) {
        console.error('Failed to parse JSON body:', e.message);
        resolve({});
      }
    });
    req.on('error', reject);
  });
}

module.exports = async (req, res) => {
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  try {
    const body = await parseBody(req);

    // Accept several possible field names from the front-end
    const optimizerJobId =
      body.optimizer_job_id ||
      body.optimizerJobId ||
      body.job_id ||
      body.jobId ||
      null;

    const slateDate = body.slate_date || body.slateDate || null;
    const actualCsv = body.actual_csv || body.actualCsv || '';

    if (!optimizerJobId || typeof optimizerJobId !== 'string' || !optimizerJobId.trim()) {
      return res.status(400).json({
        ok: false,
        error: 'Missing optimizer_job_id (optimizer_job_id / optimizerJobId / job_id)'
      });
    }

    // For now we’re not *really* using the CSVs – just stubbing learning.
    // Later we’ll parse actualCsv & compare to projections.
    console.log('📈 TRACKER received optimizer job:', optimizerJobId);
    if (slateDate) console.log('  Slate date:', slateDate);
    if (actualCsv && actualCsv.length > 0) {
      console.log('  Actual CSV length:', actualCsv.length);
    }

    // Dummy “learning” result – just echo current & suggested weights.
    const currentWeights = {
      W_SALARY_PROXY: 0.35,
      W_MATCHUP: 0.25,
      W_PACE: 0.15,
      W_REST: 0.10,
      W_OPPORTUNITY: 0.10,
      W_SENTIMENT: 0.05,
    };

    const suggestedWeights = {
      W_SALARY_PROXY: 0.32,
      W_MATCHUP: 0.27,
      W_PACE: 0.18,
      W_REST: 0.12,
      W_OPPORTUNITY: 0.08,
      W_SENTIMENT: 0.03,
    };

    return res.status(200).json({
      ok: true,
      message: 'Learning run completed (stubbed)',
      optimizer_job_id: optimizerJobId,
      slate_date: slateDate,
      current_weights: currentWeights,
      suggested_weights: suggestedWeights,
      metrics: {
        avg_mae: 5.2,
        avg_rmse: 7.1,
        samples: 15,
      },
    });
  } catch (err) {
    console.error('TRACKER error:', err);
    return res.status(500).json({
      ok: false,
      error: err.message || 'Internal server error in /api/tracker',
    });
  }
};
