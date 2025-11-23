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

  console.log("=== REQUEST DEBUG ===");
  console.log("Content-Type:", req.headers["content-type"]);
  console.log("Body type:", typeof req.body);

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

    console.log("Parsed body keys:", Object.keys(body));

    // -------------------- 2) NORMALIZE NAME HELPER --------------------
    const normalizeName = (name) =>
      typeof name === "string"
        ? name.replace(/\s+/g, " ").replace(/\s+,/g, ",").trim()
        : "";

    // -------------------- 3) LOCK INPUT (supports multiple formats) --------------------
    const lockRaw =
      body.lock_player_name ||
      body.locked_name ||
      body.lock ||
      body.lockPlayers ||
      body.lock_player ||
      body.locked_player ||
      "";

    let lockedPlayers = []; // Array of full names

    if (typeof lockRaw === "string" && lockRaw.trim()) {
      // Comma-separated string: "Stephen Curry, LeBron James"
      lockedPlayers = lockRaw
        .split(",")
        .map((name) => normalizeName(name))
        .filter(Boolean);
    } else if (Array.isArray(lockRaw)) {
      // Already an array
      lockedPlayers = lockRaw.map((name) => normalizeName(name)).filter(Boolean);
    } else if (lockRaw && typeof lockRaw === "object") {
      // Object with name property
      const name = normalizeName(
        lockRaw.full_name || lockRaw.name || lockRaw.last_name || ""
      );
      if (name) lockedPlayers = [name];
    }

    console.log("Lock raw input:", lockRaw);
    console.log("Locked players (normalized):", lockedPlayers);

    // -------------------- 4) EXCLUDE INPUT --------------------
    const excludeRaw =
      body.exclude_players ||
      body.excluded_players ||
      body.exclude ||
      body.excludePlayers ||
      body?.metadata?.excluded_last_names ||
      body?.metadata?.excluded_players ||
      "";

    let excludedPlayers = [];

    if (typeof excludeRaw === "string" && excludeRaw.trim()) {
      excludedPlayers = excludeRaw
        .split(",")
        .map((name) => normalizeName(name))
        .filter(Boolean);
    } else if (Array.isArray(excludeRaw)) {
      excludedPlayers = excludeRaw.map((name) => normalizeName(name)).filter(Boolean);
    }

    // Deduplicate case-insensitively
    const seen = new Set();
    excludedPlayers = excludedPlayers.filter((name) => {
      const key = name.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    console.log("Exclude raw input:", excludeRaw);
    console.log("Excluded players (normalized):", excludedPlayers);

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

    console.log("✅ Validation passed, calling SwarmNode...");

    // -------------------- 7) Build payload for INGEST (FIXED KEYS) --------------------
    const payload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv: csvText,
        sport: sport,
        // ✅ FIXED: Send as arrays with plural keys
        locked_players: lockedPlayers.length ? lockedPlayers : null,
        excluded_players: excludedPlayers.length ? excludedPlayers : null,
      },
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify(payload);

    console.log("SwarmNode URL:", url);
    console.log("Payload keys:", Object.keys(payload.payload));
    console.log("Locked players being sent:", payload.payload.locked_players);
    console.log("Excluded players being sent:", payload.payload.excluded_players);

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

    console.log("SwarmNode status:", response.statusCode);

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
        locked_players: lockedPlayers,
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
