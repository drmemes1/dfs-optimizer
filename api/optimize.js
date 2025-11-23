// api/optimize.js - working baseline + lock & exclude support
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
  console.log("Body is Buffer?", Buffer.isBuffer(req.body));

  try {
    // -------------------- 1) Parse body safely --------------------
    let body;

    if (req.body && typeof req.body === "object" && !Buffer.isBuffer(req.body)) {
      body = req.body;
      console.log("Body already parsed by Vercel");
    } else if (Buffer.isBuffer(req.body)) {
      const bodyStr = req.body.toString("utf8");
      console.log("Body is Buffer, length:", bodyStr.length);
      body = JSON.parse(bodyStr);
    } else if (typeof req.body === "string") {
      console.log("Body is string, length:", req.body.length);
      body = JSON.parse(req.body);
    } else {
      console.log("Body is:", req.body);
      return res.status(400).json({
        success: false,
        error: "Invalid request body",
        debug: {
          bodyType: typeof req.body,
          bodyValue: req.body,
        },
      });
    }

    console.log("Parsed body keys:", Object.keys(body));
    console.log("CSV length:", body.csv?.length || 0);
    console.log("Sport:", body.sport);

    // -------------------- 2) LOCK INPUT (full name support) --------------------
    // Front-end can send:
    // - locked_player: { last_name: "Stephen Curry" }  (old style)
    // - lock_player_name / locked_name / lock: "Stephen Curry" (new full name)
    let lockedPlayer = body.locked_player || null;

    const lockRaw =
      body.lock_player_name ||
      body.locked_name ||
      body.lock ||
      body.lockPlayers ||
      body.lock_player ||
      "";

    let lockedName = null;
    if (!lockedPlayer) {
      if (typeof lockRaw === "string" && lockRaw.trim()) {
        // Take first name if multiple comma-separated
        lockedName = lockRaw.split(",")[0].trim();
      } else if (lockRaw && typeof lockRaw === "object") {
        lockedName = lockRaw.full_name || lockRaw.name || lockRaw.last_name || null;
      }

      if (lockedName) {
        // Keep same shape INGEST already expects (last_name key),
        // but "last_name" may actually be "First Last" now.
        lockedPlayer = { last_name: lockedName };
      }
    } else {
      // If they already passed an object, try to read a display name for logging
      lockedName =
        lockedPlayer.full_name || lockedPlayer.name || lockedPlayer.last_name || null;
    }

    console.log("Lock raw:", lockRaw);
    console.log("Locked name (for display):", lockedName);
    console.log("Locked player object sent to INGEST:", lockedPlayer);

    // -------------------- 3) EXCLUDE INPUT --------------------
    // Front-end can send:
    // - exclude_players / excluded_players: "Curry, Davis"
    //   or ["Curry","Davis"] or ["Stephen Curry","Anthony Davis"]
    const parseNameList = (raw) => {
      if (!raw) return [];

      if (typeof raw === "string") {
        return raw
          .split(",")
          .map((s) => s.trim())
          .filter(Boolean);
      }

      if (Array.isArray(raw)) {
        return raw.map((s) => String(s).trim()).filter(Boolean);
      }

      return [];
    };

    const excludeRaw =
      body.exclude_players ||
      body.excluded_players ||
      body.exclude ||
      body.excludePlayers ||
      "";

     // Support legacy UI metadata payloads
    const metadataExclude =
      body?.metadata?.excluded_last_names || body?.metadata?.excluded_players;

    let excludedPlayers = [
      ...parseNameList(excludeRaw),
      ...parseNameList(metadataExclude),
    ];

    // Deduplicate while preserving order
    excludedPlayers = excludedPlayers.filter(
      (name, idx) => excludedPlayers.indexOf(name) === idx
    );

    console.log("Exclude raw:", excludeRaw);
    console.log("Excluded players list:", excludedPlayers);

    // -------------------- 4) Env checks --------------------
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE =
      process.env.SWARMNODE_BASE || "https://api.swarmnode.ai";
    const INGEST_AGENT_ID = process.env.INGEST_AGENT_ID;

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: "SWARMNODE_API_KEY not configured",
      });
    }

    if (!INGEST_AGENT_ID) {
      return res.status(500).json({
        success: false,
        error: "INGEST_AGENT_ID not configured",
      });
    }

    // -------------------- 5) Extract CSV + sport --------------------
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
        first_line: firstLine.substring(0, 100),
      });
    }

    console.log("✅ Validation passed, calling SwarmNode...");

    // -------------------- 6) Build payload for INGEST --------------------
    const payload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv: csvText,
        sport: sport,
        locked_player: lockedPlayer || null,
        excluded_players: excludedPlayers.length ? excludedPlayers : null,
      },
    };

    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/create/`;
    const postData = JSON.stringify(payload);

    console.log("SwarmNode URL:", url);
    console.log("Payload size:", postData.length, "bytes");

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
    console.log("SwarmNode body preview:", response.body.substring(0, 200));

    // -------------------- 7) Handle SwarmNode response --------------------
    if (
      response.body.trim().startsWith("<") ||
      response.body.includes("<!DOCTYPE")
    ) {
      return res.status(502).json({
        success: false,
        error: "SwarmNode returned HTML error page",
        status: response.statusCode,
        preview: response.body.substring(0, 300),
      });
    }

    let result;
    try {
      result = JSON.parse(response.body);
    } catch (e) {
      return res.status(502).json({
        success: false,
        error: "Could not parse SwarmNode response",
        status: response.statusCode,
        body: response.body.substring(0, 500),
      });
    }

    if (response.statusCode >= 200 && response.statusCode < 300) {
      console.log("✅ Success! Job ID:", result.id || result.job_id);

      return res.status(200).json({
        success: true,
        message: `${sport.toUpperCase()} optimization started`,
        job_id: result.id || result.job_id,
        total_salary: "Processing...",
        total_projection: "Processing...",
        lineup: "Optimization in progress...",
        locked_player_used: lockedName || null,
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
    console.error("Stack:", error.stack);

    return res.status(500).json({
      success: false,
      error: error.message,
      type: error.constructor.name,
      stack:
        process.env.NODE_ENV === "development" ? error.stack : undefined,
    });
  }
};
