// api/optimize.js
const fetch = require("node-fetch");

module.exports = async (req, res) => {
  // Basic CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ ok: false, error: "Method not allowed" });
  }

  const { sport, lock_player, exclude_player } = req.body || {};

  console.log("🧠 OPTIMIZE → Creating INGEST job", {
    url: "https://api.swarmnode.ai/v1/agent-executor-jobs/",
    agent_id: process.env.INGEST_AGENT_ID,
    sport,
    has_locked_player: !!lock_player,
    exclude_count: Array.isArray(exclude_player)
      ? exclude_player.length
      : (exclude_player ? 1 : 0)
  });

  try {
    const response = await fetch(
      "https://api.swarmnode.ai/v1/agent-executor-jobs/",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${process.env.SWARMNODE_API_KEY}`
        },
        body: JSON.stringify({
          agent_id: process.env.INGEST_AGENT_ID,
          payload: {
            // your ingest agent already knows how to fetch / parse the CSV;
            // we just tell it the context
            sport: sport || "nba",
            lock_player: lock_player || null,
            exclude_player: exclude_player || null
          }
        })
      }
    );

    console.log("☑️ OPTIMIZE: SwarmNode status:", response.status);

    // Read body safely even on non-2xx
    const text = await response.text();
    let data;
    try {
      data = JSON.parse(text);
    } catch {
      data = { raw: text };
    }

    if (!response.ok) {
      console.error("❌ OPTIMIZE: SwarmNode error body:", data);
      return res.status(500).json({
        ok: false,
        error: "SwarmNode error",
        details: data
      });
    }

    // Success – return the INGEST job id
    return res.status(200).json({
      ok: true,
      ingest_job_id: data.id,
      swarm_response: data
    });

  } catch (err) {
    console.error("❌ OPTIMIZE error:", err);
    return res.status(500).json({ ok: false, error: err.toString() });
  }
};
