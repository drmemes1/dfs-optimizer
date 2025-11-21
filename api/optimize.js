// api/optimize.js

const SWARMNODE_BASE = process.env.SWARMNODE_BASE || "https://api.swarmnode.ai";
const SWARMNODE_API_KEY = process.env.SWARMNODE_API_KEY;
const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID; // your INGEST agent ID

module.exports = async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ success: false, error: "Method not allowed" });
  }

  if (!SWARMNODE_API_KEY || !INGEST_AGENT_ID) {
    console.error("❌ Missing SWARMNODE_API_KEY or INGEST_AGENT_ID");
    return res.status(500).json({
      success: false,
      error: "Server misconfiguration: missing SwarmNode env vars",
    });
  }

  try {
    const body = req.body || {};
    const { csv, sport = "nba", locked_player = null, exclude_players = [] } = body;

    if (!csv || typeof csv !== "string") {
      return res.status(400).json({
        success: false,
        error: "Missing or invalid csv in request body",
      });
    }

    const payload = {
      csv,
      sport,
      locked_player,
      exclude_players,
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/`;

    console.log("🧠 OPTIMIZE → Creating INGEST job", {
      url,
      agent_id: INGEST_AGENT_ID,
      sport,
      has_locked_player: !!locked_player,
      exclude_count: exclude_players.length,
    });

    const snResponse = await fetch(url, {
      method: "POST",
      headers: {
        Authorization: `Token ${SWARMNODE_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        agent_id: INGEST_AGENT_ID,
        payload,
      }),
    });

    const text = await snResponse.text();
    let data = null;

    try {
      data = text ? JSON.parse(text) : null;
    } catch (e) {
      console.error("❌ OPTIMIZE: Failed to parse SwarmNode JSON:", e.message);
    }

    console.log("✅ OPTIMIZE: SwarmNode status:", snResponse.status);

    if (!snResponse.ok) {
      console.error("❌ OPTIMIZE: SwarmNode error body:", text);
      return res.status(500).json({
        success: false,
        error: "Failed to create optimization job on SwarmNode",
        status: snResponse.status,
      });
    }

    const jobId = data?.id || data?.job_id;
    if (!jobId) {
      console.error("❌ OPTIMIZE: No job_id in SwarmNode response:", data);
      return res.status(500).json({
        success: false,
        error: "No job_id returned from SwarmNode",
      });
    }

    console.log("🎯 OPTIMIZE: Created job", jobId);

    return res.status(200).json({
      success: true,
      job_id: jobId,
      message: "Optimization job created successfully",
    });
  } catch (err) {
    console.error("❌ OPTIMIZE: Unexpected error:", err);
    return res.status(500).json({
      success: false,
      error: err.message || "Unknown error",
    });
  }
};
