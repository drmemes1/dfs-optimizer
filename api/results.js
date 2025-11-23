// api/results.js - FIXED WITH SUGGESTIONS
const https = require("https");

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
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
        error: "Missing SWARMNODE_API_KEY or OPTIMIZER_AGENT_ID"
      });
    }

    const requestedJobId = req.query?.job_id || req.query?.execution_id || req.query?.id || null;

    const headers = {
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
    };

    const fetchJobDetail = async (jobId) => {
      const url = `${BASE}/v1/agent-executor-jobs/${jobId}/`;
      console.log("🔎 Fetching optimizer job:", url);
      const resp = await makeRequest(url, { method: "GET", headers });
      if (resp.statusCode !== 200) {
        console.log(`⚠️ Job fetch failed with status ${resp.statusCode}`);
        return null;
      }
      return JSON.parse(resp.body);
    };

    const extractReturnValue = (job) => {
      if (!job) return null;
      const exec = job.latest_execution || job.execution || {};
      return (
        job.return_value ||
        job.output ||
        job.result ||
        exec.return_value ||
        exec.output ||
        exec.result ||
        null
      );
    };

    const findLatestJobId = async () => {
      const listUrl = `${BASE}/v1/agent-executor-jobs/?agent_id=${AGENT_ID}&ordering=-created_at&limit=1`;
      console.log("🔎 Listing optimizer jobs:", listUrl);
      const listResp = await makeRequest(listUrl, { method: "GET", headers });
      console.log("List status:", listResp.statusCode);

      if (listResp.statusCode !== 200) {
        console.log("⚠️ List request failed");
        return null;
      }

      const listData = JSON.parse(listResp.body);
      const jobs = listData.results || [];
      
      if (!jobs.length) {
        console.log("⚠️ No jobs found for optimizer agent");
        return null;
      }

      console.log(`✅ Found ${jobs.length} job(s), using latest: ${jobs[0].id}`);
      return jobs[0].id;
    };

    // Determine which job ID to pull
    const jobId = requestedJobId || (await findLatestJobId());

    if (!jobId) {
      console.log("❌ No job ID available");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "No optimizer jobs found yet"
      });
    }

    console.log(`📌 Using job ID: ${jobId}`);

    const job = await fetchJobDetail(jobId);

    if (!job) {
      console.log("⚠️ Job not found or not ready");
      return res.status(200).json({
        success: true,
        status: "processing",
        job_id: jobId,
        message: "Job not ready or not found"
      });
    }

    console.log(`📊 Job status: ${job.status || 'unknown'}`);
    console.log(`📊 Has latest_execution: ${!!job.latest_execution}`);
    console.log(`📊 Has execution: ${!!job.execution}`);

    const rv = extractReturnValue(job);
    const executionId = job.latest_execution?.id || job.execution?.id || null;

    if (!rv) {
      console.log("⌛ Return value not ready yet");
      return res.status(200).json({
        success: true,
        status: job.status || "processing",
        job_id: jobId,
        execution_id: executionId,
        message: "Optimizer still computing…"
      });
    }

    console.log("✅ Return value ready for job", jobId);

    // Parse return value if it's a string
    let parsedRv = rv;
    if (typeof rv === 'string') {
      try {
        parsedRv = JSON.parse(rv);
        console.log("✅ Parsed return_value from string");
      } catch (e) {
        console.log("⚠️ Could not parse return_value as JSON");
      }
    }

    // Extract lineup data
    const lineup = parsedRv?.lineup || [];
    const stats = parsedRv?.stats || {};
    const recommendations = parsedRv?.recommendations || [];
    const lockedPlayer = parsedRv?.locked_player_used || null;
    const excludedPlayers = parsedRv?.excluded_players || [];

    console.log(`✅ Lineup has ${lineup.length} players`);
    console.log(`🔒 Locked player: ${lockedPlayer || 'none'}`);
    console.log(`❌ Excluded players: ${excludedPlayers.length > 0 ? excludedPlayers.join(', ') : 'none'}`);

    return res.status(200).json({
      success: true,
      status: job.status || "completed",
      job_id: jobId,
      execution_id: executionId,
      lineup: lineup,
      stats: stats,
      recommendations: recommendations,
      locked_player_used: lockedPlayer,
      excluded_players: excludedPlayers,
      slate_type: parsedRv?.slate_type || "Classic"
    });

  } catch (err) {
    console.error("❌ Error:", err);
    return res.status(500).json({ 
      success: false, 
      error: err.message,
      stack: process.env.NODE_ENV === 'development' ? err.stack : undefined
    });
  }
};
