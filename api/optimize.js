// api/optimize.js
const fetch = require("node-fetch");

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();

  const { sport, csv_url, lock_player, exclude_player } = req.body;

  if (!csv_url) {
    console.error("❌ OPTIMIZE: Missing csv_url");
    return res.status(400).json({ ok: false, error: "Missing csv_url" });
  }

  try {
    console.log("🧠 Creating INGEST job with CSV:", csv_url);

    const response = await fetch(
      "https://api.swarmnode.ai/v1/agent-executor-jobs/",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${process.env.SWARMNODE_API_KEY}`,
        },
        body: JSON.stringify({
          agent_id: process.env.INGEST_AGENT_ID,
          payload: {
            csv_url,
            sport: sport || "nba",
            lock_player: lock_player || null,
            exclude_player: exclude_player || null,
          },
        }),
      }
    );

    console.log("SwarmNode response status:", response.status);

    const data = await response.json();

    if (!response.ok) {
      console.error("❌ SwarmNode error:", data);
      return res.status(500).json({ ok: false, error: data });
    }

    return res.status(200).json({
      ok: true,
      ingest_job_id: data.id,
      swarm_response: data,
    });

  } catch (err) {
    console.error("❌ OPTIMIZE exception:", err);
    return res.status(500).json({ ok: false, error: err.toString() });
  }
};
