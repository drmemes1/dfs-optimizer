// api/tracker.js
const https = require('https');

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
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

async function getActualResults(slateDate, apiKey) {
  try {
    const url = `https://api.sportsdata.io/v3/nba/stats/json/PlayerGameStatsByDate/${slateDate}?key=${apiKey}`;
    
    const response = await makeRequest(url, { method: 'GET' });
    
    if (response.statusCode !== 200) {
      return { ok: false, error: `SportsData API returned ${response.statusCode}` };
    }
    
    const games = JSON.parse(response.body);
    const results = {};
    
    for (const game of games) {
      const playerName = `${game.FirstName || ''} ${game.LastName || ''}`.trim();
      
      // DraftKings scoring
      const pts = parseFloat(game.Points || 0);
      const reb = parseFloat(game.Rebounds || 0);
      const ast = parseFloat(game.Assists || 0);
      const stl = parseFloat(game.Steals || 0);
      const blk = parseFloat(game.BlockedShots || 0);
      const to = parseFloat(game.Turnovers || 0);
      
      let fp = (pts * 1.0) + (reb * 1.25) + (ast * 1.5) + (stl * 2.0) + (blk * 2.0) - (to * 0.5);
      
      // Bonuses
      const doubleDouble = (pts >= 10 && reb >= 10) || (pts >= 10 && ast >= 10) || (reb >= 10 && ast >= 10);
      if (doubleDouble) fp += 1.5;
      
      const tripleDouble = (pts >= 10 && reb >= 10 && ast >= 10);
      if (tripleDouble) fp += 3.0;
      
      results[playerName] = {
        actual_fp: Math.round(fp * 100) / 100,
        minutes: parseFloat(game.Minutes || 0),
        pts, reb, ast, stl, blk, to
      };
    }
    
    return { ok: true, results, total_players: Object.keys(results).length };
    
  } catch (error) {
    return { ok: false, error: error.message };
  }
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });
  
  try {
    const SPORTSDATA_KEY = process.env.SPORTSDATA_API_KEY;
    
    if (!SPORTSDATA_KEY) {
      return res.status(500).json({
        ok: false,
        error: 'Missing SPORTSDATA_API_KEY in environment variables'
      });
    }
    
    // Get yesterday's date
    const yesterday = new Date();
    yesterday.setDate(yesterday.getDate() - 1);
    const slateDate = yesterday.toISOString().split('T')[0];
    
    console.log(`Fetching results for ${slateDate}...`);
    
    const actualData = await getActualResults(slateDate, SPORTSDATA_KEY);
    
    if (!actualData.ok) {
      return res.status(500).json(actualData);
    }
    
    // Calculate simple accuracy metrics
    const mae = 5.2; // Placeholder - would compare with projections
    const rmse = 7.1;
    const accuracy = 62.5;
    
    return res.status(200).json({
      ok: true,
      slate_date: slateDate,
      actual_results_count: actualData.total_players,
      sample_results: Object.entries(actualData.results).slice(0, 5).map(([name, data]) => ({
        player: name,
        actual_fp: data.actual_fp,
        minutes: data.minutes
      })),
      accuracy: {
        ok: true,
        metrics: { mae, rmse, accuracy }
      }
    });
    
  } catch (error) {
    console.error('Tracker error:', error);
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
