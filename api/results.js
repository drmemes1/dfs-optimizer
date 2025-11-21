// api/results.js
const fetch = require("node-fetch");

module.exports = async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();
  if (req.method !== "GET") {
    return res.status(405).json({ success: false, error: "Method not allowed" });
  }

  const { job_id } = req.query || {};
  const SWARMNODE_BASE = process.env.SWARMNODE_BASE || "https://api.swarmnode.ai";
  const API_KEY = process.env.SWARMNODE_API_KEY;
  const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID;

  if (!API_KEY) {
    console.error("RESULTS: Missing SWARMNODE_API_KEY");
    return res.status(500).json({ success: false, error: "Missing SWARMNODE_API_KEY" });
  }

  try {
    // -------------------------------------------------------
    // 1) If job_id is provided → fetch that executor job
    // -------------------------------------------------------
    if (job_id) {
      const jobUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${job_id}/`;
      console.log("📡 RESULTS → Fetching specific executor job:", jobUrl);

      const jobRes = await fetch(jobUrl, {
        headers: { Authorization: `Bearer ${API_KEY}` },
      });

      console.log("RESULTS: executor job status:", jobRes.status);

      if (jobRes.status === 404) {
        return res.status(404).json({
          success: false,
          status: "not_found",
          error: "Job not found",
        });
      }

      const jobData = await jobRes.json();

      console.log("RESULTS: job summary:", {
        id: jobData.id,
        status: jobData.status,
        has_output: jobData.has_output,
        has_result: jobData.has_result,
        has_return_value: jobData.has_return_value,
      });

      // No return_value yet → still processing
      if (!jobData.has_return_value || !jobData.return_value) {
        return res.status(200).json({
          success: true,
          status: jobData.status || "processing",
        });
      }

      // Assume return_value already contains lineup/stats
      return res.status(200).json({
        success: true,
        status: "completed",
        ...jobData.return_value,
      });
    }

    // -------------------------------------------------------
    // 2) No job_id → grab latest OPTIMIZER executor job
    // -------------------------------------------------------
    console.log("🔍 RESULTS → Checking latest OPTIMIZER job (no job_id)");

    const listUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/?agent_id=${OPTIMIZER_AGENT_ID}&ordering=-created_at&limit=1`;
    console.log("Step 1: Listing optimizer jobs from:", listUrl);

    const listRes = await fetch(listUrl, {
      headers: { Authorization: `Bearer ${API_KEY}` },
    });

    console.log("RESULTS: list response status:", listRes.status);

    if (!listRes.ok) {
      const bodyText = await listRes.text();
      console.error("RESULTS: Failed to list optimizer jobs:", bodyText);
      return res.status(500).json({
        success: false,
        error: "Failed to list optimizer jobs",
      });
    }

    const listData = await listRes.json();
    const jobs = listData.results || listData || [];

    if (!jobs.length) {
      return res.status(200).json({
        success: true,
        status: "no_jobs",
        message: "No optimizer jobs found",
      });
    }

    const latest = jobs[0];

    console.log("RESULTS: Found", jobs.length, "optimizer job(s)");
    console.log("RESULTS: Latest OPTIMIZER job:", {
      id: latest.id,
      status: latest.status,
      agent_id: latest.agent_id,
      has_return_value: latest.has_return_value,
    });

    // If latest already has return_value, use it directly
    if (latest.has_return_value && latest.return_value) {
      return res.status(200).json({
        success: true,
        status: "completed",
        ...latest.return_value,
      });
    }

    // Otherwise fetch full details for that job
    const detailUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${latest.id}/`;
    console.log("Step 2: Retrieving optimizer job details from:", detailUrl);

    const detailRes = await fetch(detailUrl, {
      headers: { Authorization: `Bearer ${API_KEY}` },
    });

    console.log("RESULTS: detail response status:", detailRes.status);

    if (!detailRes.ok) {
      const bodyText = await detailRes.text();
      console.error("RESULTS: Failed to retrieve optimizer job:", bodyText);
      return res.status(500).json({
        success: false,
        error: "Failed to retrieve optimizer job",
      });
    }

    const detail = await detailRes.json();

    if (!detail.has_return_value || !detail.return_value) {
      return res.status(200).json({
        success: true,
        status: detail.status || "processing",
      });
    }

    return res.status(200).json({
      success: true,
      status: "completed",
      ...detail.return_value,
    });
  } catch (err) {
    console.error("❌ RESULTS error:", err);
    return res.status(500).json({
      success: false,
      status: "error",
      error: err.message || String(err),
    });
  }
};
