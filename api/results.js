// api/results.js - The correct SwarmNode execution fetcher (2025 format)
const https = require("https");

function makeRequest(url, options) {
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
      res.on("end", () => resolve({ statusCode: res.statusCode, body: data }));
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
    const KEY = process.env.SWARMNODE_API_KEY;
    const AGENT_ID = process.env.OPTIMIZER_AGENT_ID;
    const BASE = (process.env.SWARMNODE_BASE || "https://api.swarmnode.ai").replace(/\/+$/, "");

    if (!KEY || !AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: "Missing API key or AGENT_ID"
      });
    }

    console.log("🔎 Fetching latest optimizer execution…");

    // ------------------------------------------------------
    // STEP 1 — LIST EXECUTIONS FOR THIS AGENT
    // ------------------------------------------------------
    const listUrl =
      `${BASE}/v1/executions/?agent_id=${AGENT_ID}&ordering=-created_at&limit=1`;

    const listResp = await makeRequest(listUrl, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${KEY}`,
        "Content-Type": "application/json"
      }
    });

    console.log("List status:", listResp.statusCode);

    if (listResp.statusCode !== 200) {
      console.error("Failed list:", listResp.body);
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "No optimizer jobs found yet"
      });
    }

    const listData = JSON.parse(listResp.body);
    const executions = listData.results || [];

    if (executions.length === 0) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "No optimizer executions yet"
      });
    }

    const latest = executions[0];
    const execId = latest.id;

    console.log("📌 Latest execution:", execId);

    // If SwarmNode already placed return_value on the list object
    if (latest.return_value) {
      console.log("✅ Inline return_value detected");
      return res.status(200).json({
        success: true,
        status: "completed",
        ...latest.return_value,
        job_id: execId
      });
    }

    // ------------------------------------------------------
    // STEP 2 — FETCH FULL EXECUTION DETAIL
    // ------------------------------------------------------
    const detailUrl = `${BASE}/v1/executions/${execId}/`;

    const detailResp = await makeRequest(detailUrl, {
      method: "GET",
      headers: {
        Authorization: `Bearer ${KEY}`,
        "Content-Type": "application/json"
      }
    });

    console.log("Detail status:", detailResp.statusCode);

    if (detailResp.statusCode !== 200) {
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Execution still running…"
      });
    }

    const detail = JSON.parse(detailResp.body);

    // ------------------------------------------------------
    // STEP 3 — EXTRACT RETURN VALUE
    // ------------------------------------------------------
    const rv = detail.return_value || null;

    if (!rv) {
      console.log("⌛ Still no return_value yet");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Optimizer still computing…"
      });
    }

    console.log("✅ Return value ready!");

    return res.status(200).json({
      success: true,
      status: "completed",
      ...rv,
      job_id: execId
    });

  } catch (err) {
    console.error("❌ Error:", err);
    return res.status(500).json({ success: false, error: err.message });
  }
};
