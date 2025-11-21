export default async function handler(req, res) {
  try {
    console.log("📩 TRACKER received raw body:", req.body);

    if (req.method !== "POST") {
      return res.status(405).json({ error: "Method not allowed" });
    }

    const { optimizer_job_id, slate_date } = req.body || {};

    if (!optimizer_job_id) {
      console.log("❌ Missing optimizer_job_id");
      return res.status(400).json({ error: "Missing optimizer_job_id" });
    }

    console.log("📌 Parsed optimizerJobId:", optimizer_job_id);

    // =============== USE **CORRECT** SWARMNODE ENDPOINT ==================
    const agentId = process.env.OPTIMIZER_AGENT_ID;
    const apiKey = process.env.SWARMNODE_API_KEY;

    const url = `https://api.swarmnode.ai/v1/agents/${agentId}/jobs/${optimizer_job_id}`;

    console.log("🔍 Fetching optimizer job from:", url);

    const response = await fetch(url, {
      method: "GET",
      headers: {
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json"
      }
    });

    console.log("🔍 Job response status:", response.status);

    if (!response.ok) {
      const errorText = await response.text();
      console.error("❌ Failed to fetch optimizer job:", errorText);
      return res.status(500).json({
        error: "Failed to fetch optimizer job",
        details: errorText
      });
    }

    const jobData = await response.json();
    console.log("📦 Job data:", jobData);

    // Extract lineup from return_value
    const lineup = jobData?.return_value?.lineup || null;

    if (!lineup) {
      console.log("❌ No lineup found in job return_value");
      return res.status(404).json({
        error: "No lineup found in optimizer job"
      });
    }

    console.log("🏀 Parsed lineup:", lineup);

    return res.status(200).json({
      ok: true,
      optimizer_job_id,
      slate_date,
      lineup
    });

  } catch (err) {
    console.error("🔥 TRACKER error:", err);
    return res.status(500).json({ error: "Server error", details: err.message });
  }
}
