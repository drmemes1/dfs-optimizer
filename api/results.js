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
    const API_KEY = process.env.SWARMNODE_API_KEY;
    const BASE = "https://api.swarmnode.ai";
    const OPTIMIZER_ID = process.env.OPTIMIZER_AGENT_ID;
    const jobId = req.query.job_id;

    if (!jobId) {
      return res.status(400).json({ success: false, error: "Missing job_id" });
    }

    console.log(`🔍 Fetching optimizer job ${jobId}`);

    const url = `${BASE}/v1/agents/${OPTIMIZER_ID}/jobs/${jobId}/`;

    const response = await makeRequest(url, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${API_KEY}`,
        "Content-Type": "application/json",
        Accept: "application/json"
      }
    });

    console.log(`Optimizer job status code: ${response.statusCode}`);

    if (response.statusCode !== 200) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Waiting for optimizer job..."
      });
    }

    const job = JSON.parse(response.body);

    const state = job.status || "unknown";

    if (["pending", "running", "queued", "unknown"].includes(state)) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Optimizer still running..."
      });
    }

    if (["failed", "error"].includes(state)) {
      return res.status(200).json({
        success: false,
        status: "failed",
        error: job.error || "Optimizer failed"
      });
    }

    // ✔️ IMPORTANT: optimizer’s return_value contains the lineup!
    const rv = job.return_value || job.output?.return_value || job.output;

    if (!rv) {
      return res.status(200).json({
        success: false,
        status: "failed",
        error: "No return_value found in optimizer job"
      });
    }

    return res.status(200).json({
      success: true,
      status: "completed",
      ...rv,
      job_id: jobId
    });
  } catch (err) {
    console.error("❌ Error:", err);
    return res.status(500).json({ success: false, error: err.message });
  }
};
