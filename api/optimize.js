// api/optimize.js
import fetch from "node-fetch";

const SWARMNODE_BASE =
  process.env.SWARMNODE_BASE || "https://api.swarmnode.ai";
const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  try {
    if (!SWARMNODE_KEY || !INGEST_AGENT_ID) {
      console.error("Missing env vars", {
        hasKey: !!SWARMNODE_KEY,
        hasIngest: !!INGEST_AGENT_ID,
      });
      return res.status(500).json({
        ok: false,
        error:
          "Missing SWARMNODE_API_KEY or INGEST_AGENT_ID env vars on Vercel.",
      });
    }

    const { csv, sport, locked_player, exclude_player } = req.body || {};

    if (!csv || typeof csv !== "string") {
      return res
        .status(400)
        .json({ ok: false, error: "Missing or invalid CSV contents" });
    }

    console.log("🧠 OPTIMIZE → Creating INGEST job", {
      url: `${SWARMNODE_BASE}/v1/agent-executor-jobs/`,
      agent_id: INGEST_AGENT_ID,
      sport,
      has_locked_player: !!locked_player,
      exclude_count: Array.isArray(exclude_player)
        ? exclude_player.length
        : 0,
    });

    const payload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv,
        sport: sport || "nba",
        locked_player: locked_player || null,
        exclude_player: exclude_player || [],
      },
    };

    const snRes = await fetch(
      `${SWARMNODE_BASE}/v1/agent-executor-jobs/`,
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          // ❗ THIS is what fixes the 401:
          Authorization: `Bearer ${SWARMNODE_KEY}`,
        },
        body: JSON.stringify(payload),
      }
    );

    console.log("✅ OPTIMIZE: SwarmNode status:", snRes.status);

    if (!snRes.ok) {
      const text = await snRes.text();
      console.error("❌ OPTIMIZE: SwarmNode error body:", text);
      return res.status(500).json({
        ok: false,
        error: "Failed to create ingest job with SwarmNode",
        details: text,
      });
    }

    const data = await snRes.json();
    const jobId = data.id;

    console.log("🎯 OPTIMIZE: Created ingest job:", jobId);

    return res.status(200).json({
      ok: true,
      message: "Ingest job created",
      job_id: jobId,
    });
  } catch (err) {
    console.error("❌ OPTIMIZE: Unexpected error:", err);
    return res.status(500).json({
      ok: false,
      error: err.message || "Unknown error",
    });
  }
}
