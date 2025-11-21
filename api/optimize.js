const fetch = require("node-fetch");

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  console.log("🧠 OPTIMIZE → Creating INGEST job", {
    url: "https://api.swarmnode.ai/v1/agent-executor-jobs/",
    agent_id: process.env.INGEST_AGENT_ID,
    sport: req.body.sport,
    has_locked_player: !!req.body.lock_player,
    exclude_count: req.body.exclude_count || 0
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
            sport: req.body.sport || "nba",
            lock_player: req.body.lock_player || null,
            exclude_player: req.body.exclude_player || null
          }
        })
      }
    );

    console.log("☑️ OPTIMIZE: SwarmNode status:", response.status);

    const data = await response.json();

    if (!response.ok) {
      console.error("❌ OPTIMIZE: SwarmNode error body:", data);
      return res.status(500).json({
        ok: false,
        error: "SwarmNode error",
        details: data
      });
    }

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
