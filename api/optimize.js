// api/optimize.js
const fetch = require("node-fetch");

const SWARMNODE_BASE = process.env.SWARMNODE_BASE || "https://api.swarmnode.ai";
const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

module.exports = async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") {
    return res.status(405).json({ ok: false, error: "Method not allowed" });
  }

  if (!SWARMNODE_KEY || !INGEST_AGENT_ID) {
    console.error("OPTIMIZE: Missing SwarmNode env vars");
    return res
      .status(500)
      .json({ ok: false, error: "Missing SwarmNode env vars" });
  }

  const body = req.body || {};
  const csv = body.csv;
  const sport = body.sport || "nba";
  const lock_player = body.lock_player || null;      // optional
  const exclude_player = body.exclude_player || null; // optional

  if (!csv) {
    return res.status(400).json({ ok: false, error: "Missing csv" });
  }

  // Just for logging, so we don't dump full CSV
  console.log("🧠 OPTIMIZE → Creating INGEST job", {
    url: `${SWARMNODE_BASE}/v1/agent-executor-jobs/`,
    agent_id: INGEST_AGENT_ID,
    sport,
    has_csv: !!csv,
    has_locked_player: !!lock_player,
    has_exclude_player: !!exclude_player,
  });

  try {
    const createRes = await fetch(
      `${SWARMNODE_BASE}/v1/agent-executor-jobs/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${SWARMNODE_KEY}`,
        },
        body: JSON.stringify({
          agent_id: INGEST_AGENT_ID,
          payload: {
            csv,          // 🔥 this is the important part
            sport,
            lock_player,
            exclude_player,
          },
        }),
      }
    );

    console.log("☑️ OPTIMIZE: SwarmNode status:", createRes.status);

    const data = await createRes.json().catch(() => ({}));

    if (!createRes.ok) {
      console.error("❌ OPTIMIZE: SwarmNode error body:", data);
      return res.status(500).json({
        ok: false,
        error: "SwarmNode error",
        details: data,
      });
    }

    // data.id is the INGEST executor job ID
    return res.status(200).json({
      ok: true,
      ingest_job_id: data.id,
      swarm_response: data,
    });
  } catch (err) {
    console.error("❌ OPTIMIZE error:", err);
    return res
      .status(500)
      .json({ ok: false, error: err.message || String(err) });
  }
};
