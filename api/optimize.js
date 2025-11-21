const fetch = require("node-fetch");

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  try {
    const { csv_url, sport, lock_player, exclude_player } = req.body || {};

    console.log("🧠 OPTIMIZE → Creating SwarmNode execution job", {
      agent: process.env.INGEST_AGENT_ID,
      csv_url
    });

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
            sport,
            lock_player: lock_player || null,
            exclude_player: exclude_player || null
          }
        }),
      }
    );

    const text = await response.text();

    console.log("SwarmNode Raw Response:", text);

    let data;
    try {
      data = JSON.parse(text);
    } catch (err) {
      console.error("❌ JSON Parse Error — Raw response was HTML:");
      return res.status(500).json({
        ok: false,
        error: "Invalid JSON — SwarmNode returned HTML instead of JSON",
        raw: text,
      });
    }

    if (!response.ok) {
      console.error("❌ SwarmNode ERROR:", data);
      return res.status(500).json({ ok: false, error: data });
    }

    return res.status(200).json({
      ok: true,
      ingest_job_id: data.id,
      swarm_response: data,
    });
  } catch (err) {
    console.error("❌ OPTIMIZE unexpected error:", err);
    return res.status(500).json({ ok: false, error: err.message });
  }
};
