// api/results.js - COMPREHENSIVE VERSION
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

    // Determine sport from query param
    const sport = req.query?.sport || "nba";
    const AGENT_ID = sport === "nfl" ? NFL_OPTIMIZER_AGENT_ID : OPTIMIZER_AGENT_ID;

    if (!AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: `Missing ${sport.toUpperCase()}_OPTIMIZER_AGENT_ID`
      });
    }

    const requestedJobId = req.query?.job_id || null;

    const headers = {
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
    };

    console.log("\n" + "=".repeat(60));
    console.log(`RESULTS API - ${sport.toUpperCase()}`);
    console.log("=".repeat(60));

    // ========================================================================
    // STEP 1: Get the latest optimizer job ID if not provided
    // ========================================================================
    const findLatestJobId = async () => {
      const listUrl = `${BASE}/v1/agent-executor-jobs/?agent_id=${AGENT_ID}&ordering=-created_at&limit=1`;
      console.log("📋 Listing jobs:", listUrl);

      const listResp = await makeRequest(listUrl, { method: "GET", headers });
      console.log("   Status:", listResp.statusCode);

      if (listResp.statusCode !== 200) {
        console.log("   ❌ List failed");
        return null;
      }

      let listData;
      try {
        listData = JSON.parse(listResp.body);
      } catch (e) {
        console.log("   ❌ Failed to parse list response");
        return null;
      }

      // SwarmNode might return { results: [...] } or just [...]
      const jobs = listData.results || (Array.isArray(listData) ? listData : []);

      if (!jobs.length) {
        console.log("   ⚠️ No jobs found");
        return null;
      }

      console.log(`   ✅ Found ${jobs.length} job(s)`);
      console.log(`   📌 Latest job ID: ${jobs[0].id}`);
      
      return jobs[0].id;
    };

    const jobId = requestedJobId || (await findLatestJobId());

    if (!jobId) {
      console.log("❌ No job ID available\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "No optimizer jobs found yet"
      });
    }

    // ========================================================================
    // STEP 2: Fetch the job details
    // ========================================================================
    const fetchJobDetail = async (jid) => {
      const url = `${BASE}/v1/agent-executor-jobs/${jid}/`;
      console.log("\n🔍 Fetching job details:", url);

      const resp = await makeRequest(url, { method: "GET", headers });
      console.log("   Status:", resp.statusCode);

      if (resp.statusCode !== 200) {
        console.log("   ❌ Job fetch failed");
        return null;
      }

      let jobData;
      try {
        jobData = JSON.parse(resp.body);
      } catch (e) {
        console.log("   ❌ Failed to parse job response");
        return null;
      }

      // Log the full structure for debugging
      console.log("\n📦 RAW JOB OBJECT KEYS:", Object.keys(jobData));
      
      // Check all possible status locations
      const possibleStatus = 
        jobData.status ||
        jobData.state ||
        jobData.job_status ||
        jobData.execution_status ||
        (jobData.latest_execution?.status) ||
        (jobData.execution?.status) ||
        "unknown";

      console.log("   Status field:", possibleStatus);

      // Check all possible return value locations
      const rvLocations = {
        "return_value": jobData.return_value,
        "output": jobData.output,
        "result": jobData.result,
        "data": jobData.data,
        "latest_execution.return_value": jobData.latest_execution?.return_value,
        "latest_execution.output": jobData.latest_execution?.output,
        "latest_execution.result": jobData.latest_execution?.result,
        "execution.return_value": jobData.execution?.return_value,
        "execution.output": jobData.execution?.output,
        "execution.result": jobData.execution?.result,
      };

      console.log("\n📊 Return value locations:");
      for (const [key, val] of Object.entries(rvLocations)) {
        console.log(`   ${key}: ${val ? "✅ EXISTS" : "❌ null"} (type: ${typeof val})`);
      }

      return jobData;
    };

    const job = await fetchJobDetail(jobId);

    if (!job) {
      console.log("\n❌ Job not found\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        job_id: jobId,
        message: "Job not ready or not found"
      });
    }

    // ========================================================================
    // STEP 3: Extract return value from ANY possible location
    // ========================================================================
    const extractReturnValue = (jobData) => {
      // Try all possible locations
      const candidates = [
        jobData.return_value,
        jobData.output,
        jobData.result,
        jobData.data,
        jobData.latest_execution?.return_value,
        jobData.latest_execution?.output,
        jobData.latest_execution?.result,
        jobData.execution?.return_value,
        jobData.execution?.output,
        jobData.execution?.result,
      ];

      for (const candidate of candidates) {
        if (candidate) {
          console.log("\n✅ Found return value!");
          return candidate;
        }
      }

      return null;
    };

    const rawReturnValue = extractReturnValue(job);

    // Extract status
    const jobStatus = 
      job.status ||
      job.state ||
      job.latest_execution?.status ||
      job.execution?.status ||
      "unknown";

    const executionId = job.latest_execution?.id || job.execution?.id || null;

    console.log("\n📊 Extraction results:");
    console.log("   Job status:", jobStatus);
    console.log("   Has return value:", !!rawReturnValue);
    console.log("   Return value type:", typeof rawReturnValue);

    // If no return value yet
    if (!rawReturnValue) {
      console.log("\n⌛ Return value not ready yet\n");
      return res.status(200).json({
        success: true,
        status: jobStatus === "completed" ? "processing" : jobStatus,
        job_id: jobId,
        execution_id: executionId,
        message: "Optimizer still computing…"
      });
    }

    // ========================================================================
    // STEP 4: Parse return value
    // ========================================================================
    let parsedRv = rawReturnValue;

    if (typeof rawReturnValue === 'string') {
      try {
        parsedRv = JSON.parse(rawReturnValue);
        console.log("✅ Parsed return_value from JSON string");
      } catch (e) {
        console.log("⚠️ Could not parse return_value as JSON");
        console.log("   Raw value:", rawReturnValue.substring(0, 200));
      }
    }

    // ========================================================================
    // STEP 5: Extract lineup data
    // ========================================================================
    const lineup = parsedRv?.lineup || [];
    const stats = parsedRv?.stats || {};
    const recommendations = parsedRv?.recommendations || [];
    const lockedPlayer = parsedRv?.locked_player_used || null;
    const excludedPlayers = parsedRv?.excluded_players || [];

    console.log("\n📊 Final data:");
    console.log("   Lineup players:", lineup.length);
    console.log("   Locked player:", lockedPlayer || 'none');
    console.log("   Excluded players:", excludedPlayers.length);
    console.log("=".repeat(60) + "\n");

    // If lineup is empty, something is wrong
    if (!lineup || lineup.length === 0) {
      console.log("⚠️ WARNING: Lineup is empty!");
      
      return res.status(200).json({
        success: false,
        status: "error",
        error: "Lineup is empty or not found",
        job_id: jobId,
        debug: {
          has_return_value: !!rawReturnValue,
          return_value_keys: parsedRv ? Object.keys(parsedRv) : [],
          raw_return_value_preview: typeof rawReturnValue === 'string' 
            ? rawReturnValue.substring(0, 200)
            : JSON.stringify(rawReturnValue).substring(0, 200)
        }
      });
    }

    // ========================================================================
    // STEP 6: Return formatted response
    // ========================================================================
    return res.status(200).json({
      success: true,
      status: "completed",
      job_id: jobId,
      execution_id: executionId,
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
      error: err.message,
      stack: process.env.NODE_ENV === 'development' ? err.stack : undefined
    });
  }
};
