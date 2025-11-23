// api/results.js - WITH INITIAL DELAY
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

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
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

    const sport = (req.query?.sport || "nba").toLowerCase();
    const AGENT_ID = sport === "nfl" ? NFL_OPTIMIZER_AGENT_ID : OPTIMIZER_AGENT_ID;

    if (!AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: `Missing ${sport.toUpperCase()}_OPTIMIZER_AGENT_ID`
      });
    }

    const headers = {
      Authorization: `Bearer ${KEY}`,
      "Content-Type": "application/json",
    };

    console.log("\n" + "=".repeat(60));
    console.log(`📊 RESULTS API - ${sport.toUpperCase()} OPTIMIZER`);
    console.log("=".repeat(60));

    // ========================================================================
    // NEW: Check if this is the first poll (within 15 seconds of job start)
    // ========================================================================
    const skipDelay = req.query?.skip_delay === "true";
    
    if (!skipDelay) {
      console.log("\n⏳ Waiting 20 seconds for optimizer to start...");
      await sleep(10000);
      console.log("✅ Wait complete, fetching results...\n");
    } else {
      console.log("\n⚡ Skipping initial delay (subsequent poll)\n");
    }

    // ========================================================================
    // STEP 1: List executions for this agent
    // ========================================================================
    const listUrl = `${BASE}/v1/executions/?agent_id=${AGENT_ID}&ordering=-created_at&limit=3`;
    console.log("📋 Fetching latest executions:", listUrl);

    const listResp = await makeRequest(listUrl, { method: "GET", headers });
    console.log("   Status:", listResp.statusCode);

    if (listResp.statusCode !== 200) {
      console.log("   ❌ Failed to list executions\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: "Could not fetch optimizer executions"
      });
    }

    let listData;
    try {
      listData = JSON.parse(listResp.body);
    } catch (e) {
      console.log("   ❌ Failed to parse response\n");
      return res.status(502).json({
        success: false,
        error: "Invalid response from SwarmNode"
      });
    }

    const executions = listData.results || (Array.isArray(listData) ? listData : []);

    if (!executions.length) {
      console.log("   ⚠️ No executions found yet\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        message: `No ${sport.toUpperCase()} optimizer executions yet`
      });
    }

    // Show available executions for debugging
    console.log(`\n📋 Found ${executions.length} recent execution(s):`);
    executions.forEach((exec, idx) => {
      const createdAt = exec.created_at || exec.created || "unknown";
      const status = exec.status || "unknown";
      const hasRv = !!exec.return_value;
      console.log(`   ${idx + 1}. ID: ${exec.id.substring(0, 8)}... | Created: ${createdAt} | Status: ${status} | Has result: ${hasRv}`);
    });

    const latestExecution = executions[0];
    const executionId = latestExecution.id;

    console.log(`\n✅ Using execution: ${executionId}`);

    // Check if return_value is in the list response
    if (latestExecution.return_value) {
      console.log("   ✅ Return value found in list response!");

      let parsedRv = latestExecution.return_value;
      if (typeof parsedRv === 'string') {
        try {
          parsedRv = JSON.parse(parsedRv);
        } catch (e) {
          console.log("   ⚠️ Could not parse return_value");
        }
      }

      const lineup = parsedRv?.lineup || [];
      const stats = parsedRv?.stats || {};
      const recommendations = parsedRv?.recommendations || [];
      const lockedPlayer = parsedRv?.locked_player_used || null;
      const excludedPlayers = parsedRv?.excluded_players || [];

      console.log("\n✅ RESULTS:");
      console.log("   Lineup players:", lineup.length);
      console.log("   Total salary:", stats.total_salary || "N/A");
      console.log("   Locked:", lockedPlayer || "none");
      console.log("   Excluded:", excludedPlayers.length);
      console.log("=".repeat(60) + "\n");

      if (lineup.length === 0) {
        return res.status(200).json({
          success: false,
          status: "error",
          error: "Lineup is empty",
          execution_id: executionId
        });
      }

      return res.status(200).json({
        success: true,
        status: "completed",
        execution_id: executionId,
        job_id: latestExecution.job_id || executionId,
        lineup: lineup,
        stats: stats,
        recommendations: recommendations,
        locked_player_used: lockedPlayer,
        excluded_players: excludedPlayers,
        slate_type: parsedRv?.slate_type || "Classic",
        sport: sport
      });
    }

    // ========================================================================
    // STEP 2: Fetch full execution details
    // ========================================================================
    const detailUrl = `${BASE}/v1/executions/${executionId}/`;
    console.log("\n🔍 Fetching execution details:", detailUrl);

    const detailResp = await makeRequest(detailUrl, { method: "GET", headers });
    console.log("   Status:", detailResp.statusCode);

    if (detailResp.statusCode !== 200) {
      console.log("   ❌ Failed to fetch execution details\n");
      return res.status(200).json({
        success: true,
        status: "processing",
        execution_id: executionId,
        message: "Execution details not available"
      });
    }

    let execution;
    try {
      execution = JSON.parse(detailResp.body);
    } catch (e) {
      console.log("   ❌ Failed to parse execution response\n");
      return res.status(502).json({
        success: false,
        error: "Invalid execution response"
      });
    }

    console.log("📦 Execution keys:", Object.keys(execution));

    // Extract return value
    const rv = 
      execution.return_value ||
      execution.output ||
      execution.result ||
      execution.data ||
      null;

    const executionStatus = 
      execution.status ||
      execution.state ||
      "unknown";

    console.log("   Execution status:", executionStatus);
    console.log("   Has return_value:", !!rv);

    if (!rv) {
      console.log("\n⌛ No return value yet - still processing\n");
      return res.status(200).json({
        success: true,
        status: executionStatus === "completed" ? "processing" : executionStatus,
        execution_id: executionId,
        message: `${sport.toUpperCase()} optimizer still computing…`
      });
    }

    // Parse return value
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

    console.log("\n✅ RESULTS:");
    console.log("   Lineup players:", lineup.length);
    console.log("   Total salary:", stats.total_salary || "N/A");
    console.log("   Total projection:", stats.total_projection || "N/A");
    console.log("   Locked:", lockedPlayer || "none");
    console.log("   Excluded:", excludedPlayers.length);
    console.log("=".repeat(60) + "\n");

    if (lineup.length === 0) {
      return res.status(200).json({
        success: false,
        status: "error",
        error: "Lineup is empty",
        execution_id: executionId,
        debug: {
          has_rv: !!rv,
          rv_keys: parsedRv ? Object.keys(parsedRv) : [],
          rv_preview: typeof rv === 'string' ? rv.substring(0, 200) : JSON.stringify(rv).substring(0, 200)
        }
      });
    }

    return res.status(200).json({
      success: true,
      status: "completed",
      execution_id: executionId,
      job_id: execution.job_id || executionId,
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
