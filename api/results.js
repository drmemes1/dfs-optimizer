// api/results.js - FINAL VERSION
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
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID;
    const NFL_OPTIMIZER_AGENT_ID = process.env.NFL_OPTIMIZER_AGENT_ID;
    const BASE = (process.env.SWARMNODE_BASE || "https://api.swarmnode.ai").replace(/\/+$/, "");

    if (!KEY) {
      return res.status(500).json({
        success: false,
        error: "Missing SWARMNODE_API_KEY"
      });
    }

    // Determine which optimizer to check
    const sport = (req.query?.sport || "nba").toLowerCase();
    const AGENT_ID = sport === "nfl" ? NFL_OPTIMIZER_AGENT_ID : OPTIMIZER_AGENT_ID;

    if (!AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: `Missing ${sport.toUpperCase()}_OPTIMIZER_AGENT_ID environment variable`
      });
    }

    const headers = {
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
    };

    console.log("\n" + "=".repeat(60));
    console.log(`📊 RESULTS API - ${sport.toUpperCase()} OPTIMIZER`);
    console.log("   Agent ID:", AGENT_ID);
    console.log("=".repeat(60));

    // ALWAYS fetch the latest optimizer job - ignore any passed job_id
    const listUrl = `${BASE}/v1/agent-executor-jobs/?agent_id=${AGENT_ID}&ordering=-created_at&limit=1`;
    console.log("\n📋 Fetching latest job from:", listUrl);

    const listResp = await makeRequest(listUrl, { method: "GET", headers });
    console.log("   List status:", listResp.statusCode);

    if (listResp.statusCode !== 200) {
      console.log("   ❌ Failed to list jobs\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Could not fetch optimizer jobs"
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResp.body);
    } catch (e) {
      console.log("   ❌ Failed to parse list response\n");
      return res.status(502).json({
        success: false,
        error: "Invalid response from SwarmNode"
      });
    }

    const jobs = listData.results || (Array.isArray(listData) ? listData : []);

    if (!jobs.length) {
      console.log("   ⚠️ No optimizer jobs found yet\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: `No ${sport.toUpperCase()} optimizer jobs found yet`
      });
    }

    const latestJob = jobs[0];
    const jobId = latestJob.id;

    console.log("   ✅ Latest job ID:", jobId);
    console.log("   Created:", latestJob.created || "unknown");

    // Now fetch the full job details
    const jobUrl = `${BASE}/v1/agent-executor-jobs/${jobId}/`;
    console.log("\n🔍 Fetching job details:", jobUrl);

    const jobResp = await makeRequest(jobUrl, { method: "GET", headers });
    console.log("   Status:", jobResp.statusCode);

    if (jobResp.statusCode !== 200) {
      console.log("   ❌ Failed to fetch job details\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        job_id: jobId,
        message: "Job details not available"
      });
    }

    let job;
    try {
      job = JSON.parse(jobResp.body);
    } catch (e) {
      console.log("   ❌ Failed to parse job response\n");
      return res.status(502).json({
        success: false,
        error: "Invalid job response from SwarmNode"
      });
    }

    console.log("\n📦 Job object keys:", Object.keys(job));

    // Extract return value from all possible locations
    const rv = 
      job.return_value ||
      job.output ||
      job.result ||
      job.data ||
      job.latest_execution?.return_value ||
      job.latest_execution?.output ||
      job.latest_execution?.result ||
      job.execution?.return_value ||
      job.execution?.output ||
      job.execution?.result ||
      null;

    const jobStatus = 
      job.status ||
      job.state ||
      job.latest_execution?.status ||
      job.execution?.status ||
      "unknown";

    console.log("   Job status:", jobStatus);
    console.log("   Has return_value:", !!rv);

    if (!rv) {
      console.log("\n⌛ No return value yet - job still processing\n");
      return res.status(200).json({
        success: true,
        status: jobStatus === "completed" ? "processing" : jobStatus,
        job_id: jobId,
        message: `${sport.toUpperCase()} optimizer still computing…`
      });
    }

    // Parse if string
    let parsedRv = rv;
    if (typeof rv === 'string') {
      try {
        parsedRv = JSON.parse(rv);
        console.log("   ✅ Parsed return_value from JSON string");
      } catch (e) {
        console.log("   ⚠️ Return value is not valid JSON");
      }
    }

    // Extract lineup data
    const lineup = parsedRv?.lineup || [];
    const stats = parsedRv?.stats || {};
    const recommendations = parsedRv?.recommendations || [];
    const lockedPlayer = parsedRv?.locked_player_used || null;
    const excludedPlayers = parsedRv?.excluded_players || [];

    console.log("\n✅ RESULTS FOUND:");
    console.log("   Lineup players:", lineup.length);
    console.log("   Total salary:", stats.total_salary || "N/A");
    console.log("   Total projection:", stats.total_projection || "N/A");
    console.log("   Locked player:", lockedPlayer || "none");
    console.log("   Excluded:", excludedPlayers.length);
    console.log("=".repeat(60) + "\n");

    if (lineup.length === 0) {
      return res.status(200).json({
        success: false,
        status: "error",
        error: "Lineup is empty",
        job_id: jobId,
        debug: {
          has_rv: !!rv,
          rv_keys: parsedRv ? Object.keys(parsedRv) : []
        }
      });
    }

    return res.status(200).json({
      success: true,
      status: "completed",
      job_id: jobId,
      lineup: lineup,
      stats: stats,
      recommendations: recommendations,
      locked_player_used: lockedPlayer,
      excluded_players: excludedPlayers,
      slate_type: parsedRv?.slate_type || "Classic",
      sport: sport
    });

  } catch (err) {
    console.error("\n❌ ERROR:", err.message);
    console.error(err.stack);
    
    return res.status(500).json({ 
      success: false, 
      error: err.message
    });
  }
};
