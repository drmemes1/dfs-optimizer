// api/tracker.js
export default async function handler(req, res) {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "POST")
    return res.status(405).json({ error: "Method not allowed" });

  try {
    console.log("📩 TRACKER raw body:", req.body);

    const { optimizer_job_id, slate_date } = req.body;

    if (!optimizer_job_id) {
      return res
        .status(400)
        .json({ error: "Missing optimizer_job_id" });
    }

    console.log("📌 Parsed optimizerJobId:", optimizer_job_id);

    const apiKey = process.env.SWARMNODE_API_KEY;
    const optimizerAgent = process.env.OPTIMIZER_AGENT_ID;

    if (!apiKey || !optimizerAgent) {
      return res.status(500).json({
        error: "Missing SWARMNODE_API_KEY or OPTIMIZER_AGENT_ID env vars",
      });
    }

    //
    // STEP 1 — Validate that the job exists
    //
    const jobUrl = `https://api.swarmnode.ai/v1/agent-executor-jobs/${optimizer_job_id}`;
    console.log("🔍 Fetching job details from:", jobUrl);

    const jobResp = await fetch(jobUrl, {
      headers: {
        "Content-Type": "application/json",
        "x-api-key": apiKey,
      },
    });

    if (!jobResp.ok) {
      const text = await jobResp.text();
      console.error("❌ Failed to fetch optimizer job:", text);

      return res.status(500).json({
        error: "Failed to fetch optimizer job",
        details: text,
      });
    }

    const jobData = await jobResp.json();
    console.log("📦 Job retrieved:", jobData);

    //
    // STEP 2 — Extract return_value (the lineup)
    //
    const returnValue = jobData?.return_value;

    if (!returnValue) {
      return res.status(400).json({
        error: "Optimizer job exists but has no return_value yet",
      });
    }

    console.log("📊 Parsed lineup:", returnValue);

    //
    // STEP 3 — Return the lineup to frontend
    //
    return res.status(200).json({
      ok: true,
      optimizer_job_id,
      slate_date,
      lineup: returnValue,
    });

  } catch (err
