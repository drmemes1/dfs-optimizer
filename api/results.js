// api/results.js
const https = require("https");

function makeRequest(url, options = {}) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ""),
      method: options.method || "GET",
      headers: options.headers || {}
    };

    const req = https.request(reqOptions, (res) => {
      let data = "";
      res.on("data", (chunk) => (data += chunk));
      res.on("end", () =>
        resolve({ statusCode: res.statusCode, body: data })
      );
    });

    req.on("error", reject);
    req.end();
  });
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "GET, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") return res.status(200).end();

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = "https://api.swarmnode.ai";
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID;

    if (!SWARMNODE_KEY || !OPTIMIZER_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: "Missing SWARMNODE_API_KEY or OPTIMIZER_AGENT_ID env vars"
      });
    }

    console.log("🔍 Checking latest OPTIMIZER job (no ingest, optimizer only)");

    // STEP 1: list latest jobs for the OPTIMIZER agent
    const listUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/?ordering=-created_at&limit=1`;

    console.log("Step 1: Listing optimizer jobs from:", listUrl);

    const listResp = await makeRequest(listUrl, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${SWARMNODE_KEY}`,
        "Content-Type": "application/json",
        Accept: "application/json"
      }
    });

    console.log("List response status:", listResp.statusCode);

    if (listResp.statusCode !== 200) {
      console.error("Failed to list optimizer jobs:", listResp.statusCode);
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Waiting for optimizer job to appear..."
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResp.body);
    } catch (e) {
      console.error("Failed to parse list response:", e.message);
      return res.status(502).json({
        success: false,
        error: "Invalid response from SwarmNode (list)"
      });
    }

    const jobs = listData.results || listData.jobs || [];
    console.log(`Found ${jobs.length} optimizer job(s)`);

    if (!jobs.length) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "No optimizer jobs yet..."
      });
    }

    const latestJob = jobs[0];
    console.log(
      `Latest OPTIMIZER job: ${latestJob.id}, raw status=${latestJob.status}`
    );

    // STEP 2: fetch the job details to get return_value
    const jobDetailsUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/${latestJob.id}/`;

    console.log("Step 2: Retrieving optimizer job details from:", jobDetailsUrl);

    const jobResp = await makeRequest(jobDetailsUrl, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${SWARMNODE_KEY}`,
        "Content-Type": "application/json",
        Accept: "application/json"
      }
    });

    console.log("Job details response status:", jobResp.statusCode);

    if (jobResp.statusCode !== 200) {
      // If details aren’t ready yet, treat as still processing
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Optimizer still running..."
      });
    }

    let job;
    try {
      job = JSON.parse(jobResp.body);
    } catch (e) {
      console.error("Failed to parse job details:", e.message);
      return res.status(502).json({
        success: false,
        error: "Invalid response from SwarmNode (job details)"
      });
    }

    const status = job.status || "unknown";
    console.log("Optimizer job summary:", {
      id: job.id,
      status: status,
      has_output: job.has_output,
      has_result: job.has_result,
      has_return_value: job.has_return_value
    });

    // Map statuses
    if (["pending", "running", "queued", "unknown"].includes(status)) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Optimizer still running..."
      });
    }

    if (["failed", "error"].includes(status)) {
      return res.status(200).json({
        success: false,
        status: "failed",
        error: job.error || "Optimizer failed"
      });
    }

    // ✔️ The important part: grab the return_value
    const rv =
      job.return_value ||
      (job.output && job.output.return_value) ||
      job.output;

    if (!rv) {
      console.error("Job completed but no return_value found");
      return res.status(200).json({
        success: false,
        status: "failed",
        error: "Job completed but no return_value found on optimizer"
      });
    }

    // rv is exactly what your OPTIMIZER's Python main() returns
    // e.g. { ok: True, lineup: [...], stats: {...}, ... }
    return res.status(200).json({
      success: true,
      status: "completed",
      ...rv,
      job_id: job.id,
      created_at: job.created_at
    });
  } catch (error) {
    console.error("❌ Error in /api/results:", error);
    return res.status(500).json({
      success: false,
      error: error.message
    });
  }
};
