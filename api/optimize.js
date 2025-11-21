// api/optimize.js
const fetch = require("node-fetch");

module.exports = async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }
  if (req.method !== "POST") {
    return res.status(405).json({ ok: false, error: "Method not allowed" });
  }

  const { csv_url, sport, lock_player, exclude_player } = req.body || {};

  if (!csv_url) {
    return res.status(400).json({ ok: false, error: "Missing csv_url" });
  }

  const API_KEY = process.env.SWARMNODE_API_KEY;
  const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

  if (!API_KEY || !INGEST_AGENT_ID) {
    console.error("Missing envs", { API_KEY: !!API_KEY, INGEST_AGENT_ID: !!INGEST_AGENT_ID });
    return res.status(500).json({
      ok: false,
      error: "Missing SWARMNODE_API_KEY or INGEST_AGENT_ID env",
    });
  }

  try {
    console.log("🧠 OPTIMIZE → Creating INGEST job via agent-executor-jobs", {
      url: "https://api.swarmnode.ai/v1/agent-executor-jobs/",
      agent_id: INGEST_AGENT_ID,
      csv_url,
      sport,
      lock_player,
      exclude_player,
    });

    const response = await fetch("https://api.swarmnode.ai/v1/agent-executor-jobs/", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${API_KEY}`,
      },
      body: JSON.stringify({
        agent_id: INGEST_AGENT_ID,
        payload: {
          csv_url,
          sport: sport || "nba",
          lock_player: lock_player || null,
          exclude_player: exclude_player || null,
        },
      }),
    });

    const raw = await response.text();
    console.log("OPTIMIZE: raw SwarmNode response:", raw);

    let data;
    try {
      data = JSON.parse(raw);
    } catch (err) {
      console.error("❌ OPTIMIZE: SwarmNode returned non-JSON (likely HTML 404)");
      return res.status(500).json({
        ok: false,
        error: "SwarmNode returned invalid JSON",
        raw,
      });
    }

    console.log("☑️ OPTIMIZE: SwarmNode status:", response.status);

    if (!response.ok) {
      console.error("❌ OPTIMIZE: SwarmNode error body:", data);
      return res.status(500).json({
        ok: false,
        error: "SwarmNode error",
        details: data,
      });
    }

    return res.status(200).json({
      ok: true,
      ingest_job_id: data.id,
      swarm_response: data,
    });
  } catch (err) {
    console.error("❌ OPTIMIZE error:", err);
    return res.status(500).json({ ok: false, error: err.message });
  }
};
