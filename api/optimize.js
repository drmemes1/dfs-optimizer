const fetch = require("node-fetch");

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();

  const API_KEY = process.env.SWARMNODE_API_KEY;
  const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

  if (!API_KEY || !INGEST_AGENT_ID) {
    return res.status(500).json({ ok: false, error: "Missing API KEY or AGENT ID" });
  }

  try {
    console.log("🧠 OPTIMIZE → Creating job", {
      agent: INGEST_AGENT_ID,
      sport: req.body.sport,
      lock_player: req.body.lock_player,
      exclude_player: req.body.exclude_player
    });

    const response = await fetch(
      `https://api.swarmnode.ai/v1/agents/${INGEST_AGENT_ID}/execute/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${API_KEY}`
        },
        body: JSON.stringify({
          payload: {
            sport: req.body.sport || "nba",
            lock_player: req.body.lock_player || null,
            exclude_player: req.body.exclude_player || null
          }
        })
      }
    );

    const data = await response.json();
    console.log("OPTIMIZE: SwarmNode status:", response.status);

    if (!response.ok) {
      console.error("❌ SwarmNode error:", data);
      return res.status(500).json({ ok: false, error: data });
    }

    return res.status(200).json({
      ok: true,
      executor_job_id: data.executor_job_id, // THIS matters
      swarm_response: data
    });

  } catch (err) {
    console.error("❌ OPTIMIZE error:", err);
    return res.status(500).json({ ok: false, error: err.toString() });
  }
};
