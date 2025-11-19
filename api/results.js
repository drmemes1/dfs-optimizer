// api/results.js
const https = require("https");

function request(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = https.request(url, { method: "GET", headers }, (res) => {
      let data = "";
      res.on("data", (c) => (data += c));
      res.on("end", () =>
        resolve({ status: res.statusCode, body: data })
      );
    });
    req.on("error", reject);
    req.end();
  });
}

module.exports = async (req, res) => {
  res.setHeader("Access-Control-Allow-Origin", "*");

  try {
    const API_KEY = process.env.SWARMNODE_API_KEY;
    const AGENT_ID = process.env.OPTIMIZER_AGENT_ID;

    if (!API_KEY || !AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: "Missing env vars"
      });
    }

    const base = "https://api.swarmnode.ai";

    console.log("🔍 Checking latest optimizer job");

    // ✅ Correct SwarmNode endpoint
    const listUrl =
      `${base}/v1/agent-executor-jobs/` +
      `?agent_id=${AGENT_ID}&ordering=-created_at&limit=1`;

    console.log("Step 1: Listing jobs:", listUrl);

    const list = await request(listUrl, {
      Authorization: `Bearer ${API_KEY}`,
      Accept: "application/json",
    });

    console.log("List status:", list.status);

    if (list.status !== 200) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Waiting for optimizer job..."
      });
    }

    const listData = JSON.parse(list.body);
    const jobs = listData.results || listData;

    if (!jobs.length) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "No optimizer jobs found yet"
      });
    }

    const job = jobs[0];
    console.log("Latest job:", job.id, "status:", job.status);

    // ---- STEP 2: Fetch job details ----

    const detailUrl =
      `${base}/v1/agent-executor-jobs/${job.id}/`;

    console.log("Retrieving job details:", detailUrl);

    const det = await request(detailUrl, {
      Authorization: `Bearer ${API_KEY}`,
      Accept: "application/json",
    });

    if (det.status !== 200) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Optimizer still running..."
      });
    }

    const jobData = JSON.parse(det.body);

    if (["pending", "running", "unknown"].includes(jobData.status)) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Optimizer still running..."
      });
    }

    if (["failed", "error"].includes(jobData.status)) {
      return res.status(200).json({
        success: false,
        status: "failed",
        error: jobData.error || "Optimizer failed"
      });
    }

    // ---- STEP 3: Return optimizer result ----
    const rv =
      jobData.return_value ||
      (jobData.output && jobData.output.return_value) ||
      null;

    if (!rv) {
      return res.status(200).json({
        success: false,
        status: "failed",
        error: "Job completed but no return_value found"
      });
    }

    return res.status(200).json({
      success: true,
      status: "completed",
      ...rv,
      job_id: job.id
    });

  } catch (err) {
    console.error("❌ Error:", err);
    return res.status(500).json({ success: false, error: err.message });
  }
};
