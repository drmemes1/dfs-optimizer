// api/results.js
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

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const jobId = req.query.job_id;

  if (!jobId) {
    return res.status(400).json({
      success: false,
      error: 'job_id parameter is required'
    });
  }

  const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
  const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';

  if (!SWARMNODE_KEY) {
    return res.status(500).json({
      success: false,
      error: 'SWARMNODE_API_KEY not configured'
    });
  }

  try {
    console.log(`\n=== FETCHING RESULTS FOR JOB: ${jobId} ===`);
    
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
    
    const response = await makeRequest(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('SwarmNode status:', response.statusCode);

    if (response.statusCode !== 200) {
      return res.status(response.statusCode).json({
        success: false,
        error: 'Failed to fetch job status',
        status: 'error'
      });
    }

    let jobData;
    try {
      jobData = JSON.parse(response.body);
    } catch (e) {
      console.error('Failed to parse SwarmNode response:', e);
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode',
        status: 'error'
      });
    }

    console.log('Job status:', jobData.status);
    console.log('Job has return_value:', !!jobData.return_value);

    // Handle job status
    const status = jobData.status?.toLowerCase() || 'unknown';

    if (status === 'failed') {
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: jobData.error || 'Job failed'
      });
    }

    if (status === 'pending' || status === 'running' || status === 'processing') {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job still processing'
      });
    }

    if (status === 'completed' || status === 'success') {
      // Parse return_value
      let returnValue = jobData.return_value;

      // If return_value is a string, try to parse it
      if (typeof returnValue === 'string') {
        try {
          returnValue = JSON.parse(returnValue);
        } catch (e) {
          console.error('Failed to parse return_value string:', e);
        }
      }

      console.log('Return value keys:', returnValue ? Object.keys(returnValue) : 'null');

      // Check if we have valid optimizer output
      if (!returnValue || !returnValue.ok) {
        return res.status(200).json({
          success: false,
          status: 'completed',
          error: 'Job completed but no valid results',
          debug: {
            has_return_value: !!jobData.return_value,
            return_value_type: typeof jobData.return_value,
            ok_field: returnValue?.ok
          }
        });
      }

      // Extract lineup data
      const lineup = returnValue.lineup || [];
      const stats = returnValue.stats || {};
      const recommendations = returnValue.recommendations || [];
      const lockedPlayer = returnValue.locked_player_used || null;
      const excludedPlayers = returnValue.excluded_players || [];

      console.log('✅ Found lineup with', lineup.length, 'players');
      console.log('🔒 Locked player:', lockedPlayer);
      console.log('❌ Excluded players:', excludedPlayers);

      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: lineup,
        stats: stats,
        recommendations: recommendations,
        locked_player_used: lockedPlayer,
        excluded_players: excludedPlayers,
        slate_type: returnValue.slate_type || 'Classic',
        job_id: jobId
      });
    }

    // Unknown status
    return res.status(200).json({
      success: false,
      status: 'unknown',
      error: `Unknown job status: ${status}`,
      debug: {
        raw_status: jobData.status,
        has_return_value: !!jobData.return_value
      }
    });

  } catch (error) {
    console.error('❌ Error fetching results:', error);
    
    return res.status(500).json({
      success: false,
      status: 'error',
      error: error.message
    });
  }
};
