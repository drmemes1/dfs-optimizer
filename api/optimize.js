// api/optimize.js - FIXED VERSION
const https = require("https");

function makeRequest(url, options, postData) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ""),
      method: options.method || "POST",
      headers: options.headers || {},
    };

    const req = https.request(reqOptions, (res) => {
      let data = "";
      res.on("data", (chunk) => {
        data += chunk;
      });
      res.on("end", () => {
        resolve({ statusCode: res.statusCode, body: data });
      });
    });

    req.on("error", reject);
    if (postData) req.write(postData);
    req.end();
  });
}

module.exports = async (req, res) => {
  // CORS
  res.setHeader("Access-Control-Allow-Origin", "*");
  res.setHeader("Access-Control-Allow-Methods", "POST, OPTIONS");
  res.setHeader("Access-Control-Allow-Headers", "Content-Type");

  if (req.method === "OPTIONS") {
    return res.status(200).end();
  }

  if (req.method !== "POST") {
    return res.status(405).json({ success: false, error: "Method not allowed" });
  }

  try {
    // -------------------- 1) Parse body safely --------------------
    let body;

    if (req.body && typeof req.body === "object" && !Buffer.isBuffer(req.body)) {
      body = req.body;
    } else if (Buffer.isBuffer(req.body)) {
      const bodyStr = req.body.toString("utf8");
      body = JSON.parse(bodyStr);
    } else if (typeof req.body === "string") {
      body = JSON.parse(req.body);
    } else {
      return res.status(400).json({
        success: false,
        error: "Invalid request body",
      });
    }

    console.log("\n=== OPTIMIZE.JS DEBUG ===");
    console.log("Received body keys:", Object.keys(body));
    console.log("Full body:", JSON.stringify(body, null, 2));

    // -------------------- 2) Helper to normalize names --------------------
    const normalizeName = (name) =>
      typeof name === "string"
        ? name.replace(/\s+/g, " ").replace(/\s+,/g, ",").trim()
        : "";

    // -------------------- 3) LOCK INPUT (SINGLE PLAYER) --------------------
    let lockedPlayerName = null;

    // Check all possible lock keys
    const lockKeys = [
      "lock",
      "locked_player",
      "lock_player",
      "lock_player_name",
      "locked_name",
      "lockPlayer",
      "locked_players", // Also check plural in case frontend sends array
      "lock_players",
    ];

    for (const key of lockKeys) {
      const val = body[key];
      
      if (!val) continue;

      console.log(`Checking lock key "${key}":`, val, `(type: ${typeof val})`);

      if (typeof val === "string" && val.trim()) {
        lockedPlayerName = normalizeName(val);
        console.log(`✅ Found lock from key "${key}": "${lockedPlayerName}"`);
        break;
      } else if (Array.isArray(val) && val.length > 0) {
        // Take first if array
        lockedPlayerName = normalizeName(String(val[0]));
        console.log(`✅ Found lock from array "${key}"[0]: "${lockedPlayerName}"`);
        break;
      } else if (typeof val === "object" && val !== null) {
        // Object format: {name: "...", last_name: "..."}
        const name = val.full_name || val.name || val.last_name || "";
        if (name) {
          lockedPlayerName = normalizeName(name);
          console.log(`✅ Found lock from object "${key}": "${lockedPlayerName}"`);
          break;
        }
      }
    }

    console.log("Final locked player name:", lockedPlayerName || "NONE");

    // -------------------- 4) EXCLUDE INPUT (MULTIPLE PLAYERS) --------------------
    let excludedPlayers = [];

    // Check all possible exclude keys
    const excludeKeys = [
      "exclude",
      "excluded_players",
      "exclude_players",
      "excludePlayers",
      "excluded",
    ];

    for (const key of excludeKeys) {
      const val = body[key];
      
      if (!val) continue;

      console.log(`Checking exclude key "${key}":`, val, `(type: ${typeof val})`);

      if (typeof val === "string" && val.trim()) {
        // Comma-separated string
        excludedPlayers = val
          .split(",")
          .map((name) => normalizeName(name))
          .filter(Boolean);
        console.log(`✅ Found excludes from string "${key}": [${excludedPlayers.join(", ")}]`);
        break;
      } else if (Array.isArray(val) && val.length > 0) {
        excludedPlayers = val
          .map((name) => normalizeName(String(name)))
          .filter(Boolean);
        console.log(`✅ Found excludes from array "${key}": [${excludedPlayers.join(", ")}]`);
        break;
      }
    }

    // Deduplicate case-insensitively
    const seen = new Set();
    excludedPlayers = excludedPlayers.filter((name) => {
      const key = name.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    console.log("Final excluded players:", excludedPlayers.length > 0 ? excludedPlayers : "NONE");

    // -------------------- 5) Env checks --------------------
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || "https://api.swarmnode.ai";
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    if (!SWARMNODE_KEY || !INGEST_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: "Missing SWARMNODE_API_KEY or INGEST_AGENT_ID",
      });
    }

    // -------------------- 6) Extract CSV + sport --------------------
    const csvText = body.csv || "";
    const sport = body.sport || "nba";

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        success: false,
        error: "CSV is empty or too short",
        csv_length: csvText.length,
      });
    }

    const firstLine = csvText.split("\n")[0].toLowerCase();
    if (!firstLine.includes("name") || !firstLine.includes("salary")) {
      return res.status(400).json({
        success: false,
        error: "CSV must contain Name and Salary columns",
      });
    }

    console.log("✅ CSV validation passed");

    // -------------------- 7) Build payload for INGEST --------------------
    const swarmPayload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv: csvText,
        sport: sport,
        // Send single lock (string or null)
        locked_players: lockedPlayerName || null,
        // Send multiple excludes (array or null)
        excluded_players: excludedPlayers.length > 0 ? excludedPlayers : null,
      },
    };

    console.log("\n=== SENDING TO SWARMNODE ===");
    console.log("Payload keys:", Object.keys(swarmPayload.payload));
    console.log("locked_players:", swarmPayload.payload.locked_players);
    console.log("excluded_players:", swarmPayload.payload.excluded_players);
    console.log("===========================\n");

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify(swarmPayload);

    const response = await makeRequest(
      url,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${SWARMNODE_KEY}`,
          "Content-Type": "application/json",
          "Content-Length": Buffer.byteLength(postData),
        },
      },
      postData
    );

    console.log("SwarmNode response status:", response.statusCode);

    // -------------------- 8) Handle SwarmNode response --------------------
    if (response.body.trim().startsWith("<") || response.body.includes("<!DOCTYPE")) {
      return res.status(502).json({
        success: false,
        error: "SwarmNode returned HTML error page",
        status: response.statusCode,
      });
    }

    let result;
    try {
      result = JSON.parse(response.body);
    } catch (e) {
      return res.status(502).json({
        success: false,
        error: "Could not parse SwarmNode response",
        body: response.body.substring(0, 500),
      });
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      console.log("✅ Success! Job ID:", result.id || result.job_id);

      return res.status(200).json({
        success: true,
        message: `${sport.toUpperCase()} optimization started`,
        job_id: result.id || result.job_id,
        locked_player: lockedPlayerName,
        excluded_players: excludedPlayers,
        swarmnode_link: "https://app.swarmnode.ai",
      });
    }

    return res.status(response.statusCode).json({
      success: false,
      error: "SwarmNode error",
      status: response.statusCode,
      details: result,
    });
  } catch (error) {
    console.error("❌ Error:", error.message);

    return res.status(500).json({
      success: false,
      error: error.message,
      type: error.constructor.name,
    });
  }
};
