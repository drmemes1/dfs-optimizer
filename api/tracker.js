// api/tracker.js

// Simple in-memory store for tracked slates (resets when function cold-starts)
let trackedSlates = [];

module.exports = async (req, res) => {
  // CORS headers
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  // Preflight
  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ ok: false, error: "Method not allowed" });
  }

  try {
    // Vercel sometimes gives you a JSON string, sometimes an object.
    let body = req.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch (e) {
        console.error("TRACKER: Failed to parse JSON body:", e.message);
        return res.status(400).json({
          ok: false,
          error: "Invalid JSON body",
        });
      }
    }

    const optimizer_job_id = (body.optimizer_job_id || "").trim();
    const slate_date = body.slate_date || null;

    console.log("🧠 TRACKER received optimizer job:", optimizer_job_id);
    console.log("🗓  Slate date:", slate_date);

    if (!optimizer_job_id) {
      return res.status(400).json({
        ok: false,
        error: "Missing optimizer_job_id",
      });
    }

    // In the future we can also accept:
    // - body.actual_results_csv
    // - body.winning_lineup_csv
    // and compute real MAE/RMSE, etc.

    // For now, create a simple record of this slate
    const record = {
      optimizer_job_id,
      slate_date,
      created_at: new Date().toISOString(),
      // placeholders for future learning metrics
      avg_projection_error: null,
      slate_score: null,
      winning_score: null,
    };

    trackedSlates.push(record);

    // Respond with something the dashboard/learner can use
    return res.status(200).json({
      ok: true,
      message: "Tracker received optimizer job",
      record,
      total_tracked_slates: trackedSlates.length,
    });
  } catch (err) {
    console.error("TRACKER error:", err);
    return res.status(500).json({
      ok: false,
      error: "Unknown error",
      details: err.message,
    });
  }
};
