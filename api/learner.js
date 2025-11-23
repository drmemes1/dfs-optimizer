// api/learner.js - Compare optimizer projections vs. actuals for learning
const https = require('https');

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ''),
      method: options.method || 'GET',
      headers: options.headers || {},
    };

    const req = https.request(reqOptions, (res) => {
      let data = '';
      res.on('data', (chunk) => (data += chunk));
      res.on('end', () => resolve({ statusCode: res.statusCode, body: data }));
    });

    req.on('error', reject);
    req.end();
  });
}

function normalizeNumber(val) {
  if (val === null || val === undefined) return null;
  const num = typeof val === 'string' ? parseFloat(val.replace(/[^0-9.-]/g, '')) : Number(val);
  return Number.isFinite(num) ? num : null;
}

function normalizeLineup(list) {
  if (!Array.isArray(list)) return [];
  return list
    .map((p) => ({
      slot: p.slot || p.position || null,
      name: p.name || '',
      team: p.team || p.team_abbrev || null,
      salary: p.salary,
      salary_num: normalizeNumber(p.salary_num ?? p.salary),
      projection: normalizeNumber(p.projection ?? p.proj_fp ?? p.fppg ?? p.projected_points),
      value: p.value_score || p.value || null,
      is_locked: !!p.is_locked,
      actual_fp: normalizeNumber(p.actual_fp ?? p.actual_points ?? p.fp_actual),
    }))
    .filter((p) => p.name);
}

function mergeActuals(projected, withActuals) {
  const actualMap = new Map();
  withActuals.forEach((p) => {
    if (p.name) {
      actualMap.set(p.name.toLowerCase(), p.actual_fp);
    }
  });

  return projected.map((p) => {
    const actual = actualMap.get((p.name || '').toLowerCase());
    return { ...p, actual_fp: actual ?? p.actual_fp ?? null };
  });
}

function computeStats(lineup) {
  const withActuals = lineup.filter((p) => p.actual_fp !== null && p.projection !== null);
  const totalProjected = withActuals.reduce((sum, p) => sum + (p.projection || 0), 0);
  const totalActual = withActuals.reduce((sum, p) => sum + (p.actual_fp || 0), 0);
  const diffs = withActuals.map((p) => (p.actual_fp || 0) - (p.projection || 0));
  const mae = diffs.length
    ? diffs.reduce((sum, d) => sum + Math.abs(d), 0) / diffs.length
    : null;
  const mse = diffs.length ? diffs.reduce((sum, d) => sum + d * d, 0) / diffs.length : null;

  return {
    player_count: lineup.length,
    compared_count: withActuals.length,
    missing_actuals: lineup.length - withActuals.length,
    total_projected: Number.isFinite(totalProjected) ? totalProjected : null,
    total_actual: Number.isFinite(totalActual) ? totalActual : null,
    total_diff: Number.isFinite(totalActual) && Number.isFinite(totalProjected)
      ? totalActual - totalProjected
      : null,
    mae: Number.isFinite(mae) ? mae : null,
    mse: Number.isFinite(mse) ? mse : null,
  };
}

function buildRecommendations(stats) {
  const recs = [];
  if (stats.mae !== null) {
    if (stats.mae > 6) {
      recs.push('High average error — consider raising matchup/pace weights for volatile games.');
    } else if (stats.mae > 3) {
      recs.push('Moderate error — small tweaks to salary/value weighting may help.');
    } else {
      recs.push('Projections were close; keep current weights but monitor injury adjustments.');
    }
  }

  if (stats.total_diff !== null) {
    if (stats.total_diff > 0) {
      recs.push('Actual lineup beat projections; model may be conservative. Increase upside weighting.');
    } else if (stats.total_diff < 0) {
      recs.push('Actual lineup underperformed projections; reduce over-confident weights.');
    }
  }

  return recs;
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ ok: false, error: 'Method not allowed' });

  try {
  let body = req.body;
    if (Buffer.isBuffer(body)) {
      body = JSON.parse(body.toString('utf8'));
    } else if (typeof body === 'string') {
      body = JSON.parse(body);
    }
    if (!body || typeof body !== 'object') {
      return res.status(400).json({ ok: false, error: 'Invalid JSON body' });
    }

    const jobId = body.job_id || body.optimizer_job_id || body.execution_id;
    const userLineup = normalizeLineup(body.lineup || body.players || []);
    const winningTotal = normalizeNumber(body.winning_total);
    const yourTotal = normalizeNumber(body.your_total);
    const winningNotes = body.winning_notes || body.notes || '';

    if (!jobId) {
      return res.status(400).json({ ok: false, error: 'Missing job_id/optimizer_job_id' });
    }

    const apiKey = process.env.SWARMNODE_API_KEY;
    const base = (process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai').replace(/\/+$/, '');

    let jobLineup = [];
    let jobStatus = null;

    if (apiKey) {
      try {
        const detailUrl = `${base}/v1/agent-executor-jobs/${jobId}/`;
        console.log('🔎 Fetching optimizer job for learner:', detailUrl);
        const resp = await makeRequest(detailUrl, {
          method: 'GET',
          headers: {
            Authorization: `Bearer ${apiKey}`,
            'Content-Type': 'application/json',
          },
        });

        if (resp.statusCode === 200) {
          const job = JSON.parse(resp.body);
          jobStatus = job.status;
          const rv = job.return_value || job.output || job.result || {};
          jobLineup = normalizeLineup(rv.lineup || []);
        } else {
          console.warn('Learner: unable to fetch job detail, status', resp.statusCode);
        }
      } catch (err) {
        console.warn('Learner: job fetch error', err.message);
      }
    } else {
      console.warn('Learner: SWARMNODE_API_KEY missing — skipping job fetch');
    }

    const baselineLineup = jobLineup.length ? jobLineup : userLineup;
    const mergedLineup = mergeActuals(baselineLineup, userLineup);

    const stats = computeStats(mergedLineup);
    const recommendations = buildRecommendations(stats);
    
    return res.status(200).json({
      ok: true,
      job_id: jobId,
      job_status: jobStatus,
      compared_lineup: mergedLineup,
      stats,
      winning_total: winningTotal,
      your_total: yourTotal,
      winning_notes: winningNotes,
      recommendations,
      summary:
        stats.mae !== null
          ? `MAE ${stats.mae.toFixed(2)} pts across ${stats.compared_count} players`
          : 'No actual fantasy points provided yet.',
    });
    
  } catch (error) {
    console.error('❌ learner error:', error);
    return res.status(500).json({ ok: false, error: error.message });
  }
};
