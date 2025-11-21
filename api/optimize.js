// api/optimize.js

// Vercel Node 18+ has global `fetch` – no need for node-fetch

module.exports = async function handler(req, res) {
  // Basic CORS in case you ever hit this from another origin
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  if (req.method !== 'POST') {
    return res.status(405).json({ success: false, error: 'Method not allowed' });
  }

  try {
    const SWARMNODE_BASE =
      process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const SWARMNODE_API_KEY = process.env.SWARMNODE_API_KEY;
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    if (!SWARMNODE_API_KEY) {
      console.error('❌ OPTIMIZE: Missing SWARMNODE_API_KEY');
      return res
        .status(500)
        .json({ success: false, error: 'Missing SWARMNODE_API_KEY' });
    }

    if (!INGEST_AGENT_ID) {
      console.error('❌ OPTIMIZE: Missing INGEST_AGENT_ID');
      return res
        .status(500)
        .json({ success: false, error: 'Missing INGEST_AGENT_ID' });
    }

    // Vercel sometimes gives you a parsed object, sometimes a string – handle both
    const body =
      typeof req.body === 'string' ? JSON.parse(req.body || '{}') : req.body || {};

    const { csv, sport = 'nba', locked_player, excluded_players } = body;

    if (!csv || typeof csv !== 'string' || csv.trim().length === 0) {
      return res
        .status(400)
        .json({ success: false, error: 'Missing or empty CSV in request' });
    }

    console.log('📥 OPTIMIZE: Received CSV length:', csv.length);
    console.log('📥 OPTIMIZE: Sport:', sport);
    console.log(
      '📥 OPTIMIZE: Lock/exclude:',
      !!locked_player,
      Array.isArray(excluded_players) ? excluded_players.length : 0
    );

    // Build the payload that goes to your INGEST agent
    const ingestPayload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv,
        sport,
        // keep names stable so your INGEST agent script can read them
        locked_player: locked_player || null,
        excluded_players: excluded_players || [],
      },
    };

    console.log('🧠 OPTIMIZE → Creating INGEST job', {
      url: `${SWARMNODE_BASE}/v1/agent-executor-jobs/`,
      agent_id: INGEST_AGENT_ID,
      sport,
      has_locked_player: !!locked_player,
      exclude_count: Array.isArray(excluded_players)
        ? excluded_players.length
        : 0,
    });

    const snResponse = await fetch(
      `${SWARMNODE_BASE}/v1/agent-executor-jobs/`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          Authorization: `Bearer ${SWARMNODE_API_KEY}`,
        },
        body: JSON.stringify(ingestPayload),
      }
    );

    console.log('✅ OPTIMIZE: SwarmNode status:', snResponse.status);

    const snData = await snResponse.json().catch(() => ({}));

    if (!snResponse.ok) {
      console.error(
        '❌ OPTIMIZE: SwarmNode error body:',
        JSON.stringify(snData, null, 2)
      );
      return res.status(snResponse.status).json({
        success: false,
        error: snData.message || 'Failed to create ingest job on SwarmNode',
        raw: snData,
      });
    }

    // SwarmNode usually returns the job with an `id` field
    const jobId = snData.id || snData.job_id || snData.job?.id;

    console.log('🎯 OPTIMIZE: Created INGEST job id:', jobId);

    if (!jobId) {
      return res.status(500).json({
        success: false,
        error: 'Missing job ID in SwarmNode response',
        raw: snData,
      });
    }

    // This is what your front-end expects:
    // { success: true, job_id: "...", message?: "..."}
    return res.status(200).json({
      success: true,
      job_id: jobId,
      message: 'Ingest job created successfully',
    });
  } catch (err) {
    console.error('❌ OPTIMIZE: Unexpected error:', err);
    return res.status(500).json({
      success: false,
      error: err.message || 'Unexpected server error',
    });
  }
};
