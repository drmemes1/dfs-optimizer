// /api/tracker.js
module.exports = async (req, res) => {
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
    // ---- IMPORTANT: force JSON parsing ----
    let body = req.body;
    if (typeof body === "string") {
      try {
        body = JSON.parse(body);
      } catch (err) {
        return res.status(400).json({
          error: "Invalid JSON body",
          received_body: body
        });
      }
    }

    const { optimizer_job_id, slate_date } = body || {};

    console.log("📥 TRACKER received body:", body);

    // ---- VALIDATION ----
    if (!optimizer_job_id || optimizer_job_id.trim() === "") {
      console.log("❌ ERROR: Missing optimizer_job_id");
      return res.status(400).json({ error: "Missing optimizer_job_id" });
    }

    if (!slate_date) {
      console.log("❌ ERROR: Missing slate_date");
      return res.status(400).json({ error: "Missing slate_date" });
    }

    console.log("🧠 TRACKER processing optimizer job:", optimizer_job_id);
    console.log("📅 Slate date:", slate_date);

    // --- Placeholder: Next step is connecting to SwarmNode to retrieve lineup ---
    // For now we store stub data so learner can function.

    return res.status(200).json({
      ok: true,
      message: "Tracker received optimizer job + slate date successfully",
      optimizer_job_id,
      slate_date
    });
  } catch (error) {
    console.error("Tracker error:", error);
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
