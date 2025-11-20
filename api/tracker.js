// api/tracker.js
export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST") {
    return res.status(405).json({ error: "Method not allowed" });
  }

  try {
    const { optimizer_job_id, actual_points, winning_points } = req.body;

    if (!optimizer_job_id) {
      return res.status(400).json({ error: "Missing optimizer_job_id" });
    }

    // Validate inputs
    if (!Array.isArray(actual_points) || actual_points.length === 0) {
      return res.status(400).json({ error: "Missing actual player results" });
    }

    if (!Array.isArray(winning_points) || winning_points.length === 0) {
      return res.status(400).json({ error: "Missing winning lineup" });
    }

    // === Fetch optimizer return value ===
    const optimizerResp = await fetch(
      `https://api.swarmnode.ai/v1/agent-executor-jobs/${optimizer_job_id}/return_value`,
      {
        headers: {
          "x-api-key": process.env.SWARMNODE_API_KEY,
          "Content-Type": "application/json"
        }
      }
    );

    if (!optimizerResp.ok) {
      return res.status(400).json({
        error: "Failed to retrieve optimizer job return value"
      });
    }

    const optimizerData = await optimizerResp.json();

    // === Calculate projection errors ===
    const projections = optimizerData.lineup || [];

    const errors = projections.map((player) => {
      const actual = actual_points.find(p => p.name === player.name);
      return {
        name: player.name,
        proj: player.projection,
        actual: actual ? actual.points : null,
        error: actual ? actual.points - player.projection : null
      };
    });

    // === Save tracking data to return to Learner ===
    return res.status(200).json({
      ok: true,
      optimizer_job_id,
      projections,
      actual_points,
      winning_points,
      errors
    });

  } catch (err) {
    return res.status(500).json({
      ok: false,
      error: err.message
    });
  }
}
