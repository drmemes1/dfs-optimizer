// api/optimize.js - ROTOGRINDERS VERSION
// Updated to parse RotoGrinders CSV format directly

const https = require("https");

// Minimal CSV splitter that respects quotes
function splitCsvLine(line, delim = ",") {
  const cells = [];
  let cur = "";
  let inQuotes = false;

  for (let i = 0; i < line.length; i++) {
    const ch = line[i];

    if (ch === '"') {
      if (inQuotes && line[i + 1] === '"') {
        cur += '"';
        i++;
      } else {
        inQuotes = !inQuotes;
      }
      continue;
    }

    if (ch === delim && !inQuotes) {
      cells.push(cur.trim());
      cur = "";
    } else {
      cur += ch;
    }
  }

  cells.push(cur.trim());
  return cells;
}

// Validate RotoGrinders CSV format
function validateRotoGrindersCsv(csvText) {
  const lines = (csvText || "").replace(/\r\n/g, "\n").split("\n").filter(Boolean);
  
  if (lines.length < 2) {
    return { valid: false, error: "CSV has fewer than 2 lines" };
  }

  const header = lines[0].toLowerCase();
  
  // Check for required RotoGrinders columns
  const requiredCols = ["name", "salary", "fpts", "pos", "team"];
  const missingCols = requiredCols.filter(col => !header.includes(col));
  
  if (missingCols.length > 0) {
    return { 
      valid: false, 
      error: `Missing required columns: ${missingCols.join(", ")}. Make sure you're uploading a RotoGrinders export.`
    };
  }

  // Optional but recommended columns
  const optionalCols = ["floor", "ceil", "minutes", "smash", "proj_own"];
  const presentOptional = optionalCols.filter(col => header.includes(col));
  
  return { 
    valid: true, 
    playerCount: lines.length - 1,
    hasOptional: presentOptional
  };
}

// Quick stats from CSV
function getQuickStats(csvText) {
  const lines = (csvText || "").replace(/\r\n/g, "\n").split("\n").filter(Boolean);
  if (lines.length < 2) return {};

  const header = splitCsvLine(lines[0]).map(h => h.toLowerCase());
  const salaryIdx = header.indexOf("salary");
  const fptsIdx = header.indexOf("fpts");
  const teamIdx = header.indexOf("team");

  let totalPlayers = 0;
  let avgSalary = 0;
  let avgProj = 0;
  const teams = new Set();

  for (let i = 1; i < lines.length; i++) {
    const row = splitCsvLine(lines[i]);
    const salary = parseFloat((row[salaryIdx] || "0").replace(/[$,]/g, ""));
    const proj = parseFloat(row[fptsIdx] || "0");
    const team = row[teamIdx] || "";

    if (salary > 0) {
      totalPlayers++;
      avgSalary += salary;
      avgProj += proj;
      if (team) teams.add(team.toUpperCase());
    }
  }

  return {
    playerCount: totalPlayers,
    avgSalary: totalPlayers > 0 ? Math.round(avgSalary / totalPlayers) : 0,
    avgProjection: totalPlayers > 0 ? (avgProj / totalPlayers).toFixed(1) : 0,
    teamCount: teams.size,
    teams: Array.from(teams).sort()
  };
}

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

    console.log("\n=== OPTIMIZE.JS (RotoGrinders) ===");
    console.log("Received body keys:", Object.keys(body));

    // -------------------- 2) Helper to normalize names --------------------
    const normalizeName = (name) =>
      typeof name === "string"
        ? name.replace(/\s+/g, " ").replace(/\s+,/g, ",").trim()
        : "";

    // -------------------- 3) LOCK INPUT (SINGLE PLAYER) --------------------
    let lockedPlayerName = null;

    const lockKeys = [
      "lock", "locked_player", "lock_player", "lock_player_name",
      "locked_name", "lockPlayer", "locked_players", "lock_players",
    ];

    for (const key of lockKeys) {
      const val = body[key];
      if (!val) continue;

      if (typeof val === "string" && val.trim()) {
        lockedPlayerName = normalizeName(val);
        console.log(`✅ Found lock: "${lockedPlayerName}"`);
        break;
      } else if (Array.isArray(val) && val.length > 0) {
        lockedPlayerName = normalizeName(String(val[0]));
        console.log(`✅ Found lock from array: "${lockedPlayerName}"`);
        break;
      }
    }

    // -------------------- 4) EXCLUDE INPUT (MULTIPLE PLAYERS) --------------------
    let excludedPlayers = [];

    const excludeKeys = [
      "exclude", "excluded_players", "exclude_players", "excludePlayers", "excluded",
    ];

    for (const key of excludeKeys) {
      const val = body[key];
      if (!val) continue;

      if (typeof val === "string" && val.trim()) {
        excludedPlayers = val.split(",").map(n => normalizeName(n)).filter(Boolean);
        break;
      } else if (Array.isArray(val) && val.length > 0) {
        excludedPlayers = val.map(n => normalizeName(String(n))).filter(Boolean);
        break;
      }
    }

    // Deduplicate
    const seen = new Set();
    excludedPlayers = excludedPlayers.filter((name) => {
      const key = name.toLowerCase();
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });

    console.log("Locked:", lockedPlayerName || "NONE");
    console.log("Excluded:", excludedPlayers.length > 0 ? excludedPlayers : "NONE");

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

    // -------------------- 6) Extract and Validate CSV --------------------
    const csvText = body.csv || "";
    const mode = (body.mode || "CASH").toUpperCase();
    const sport = body.sport || "nba";

    if (!csvText || csvText.length < 50) {
      return res.status(400).json({
        success: false,
        error: "CSV is empty or too short",
        csv_length: csvText.length,
      });
    }

    // Validate RotoGrinders format
    const validation = validateRotoGrindersCsv(csvText);
    if (!validation.valid) {
      return res.status(400).json({
        success: false,
        error: validation.error,
        hint: "Make sure you're uploading a RotoGrinders CSV export with columns: name, salary, fpts, pos, team"
      });
    }

    // Get quick stats
    const stats = getQuickStats(csvText);
    console.log("CSV Stats:", stats);

    console.log("✅ RotoGrinders CSV validation passed");
    console.log(`   Players: ${validation.playerCount}`);
    console.log(`   Optional columns present: ${validation.hasOptional.join(", ")}`);

    // -------------------- 7) Build payload for INGEST --------------------
    const swarmPayload = {
      agent_id: INGEST_AGENT_ID,
      payload: {
        csv: csvText,
        sport: sport,
        mode: mode,
        locked_players: lockedPlayerName || null,
        excluded_players: excludedPlayers.length > 0 ? excludedPlayers : null,
      },
    };

    console.log("\n=== SENDING TO SWARMNODE ===");
    console.log("Mode:", mode);
    console.log("Locked:", swarmPayload.payload.locked_players);
    console.log("Excluded:", swarmPayload.payload.excluded_players);

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
        message: `${sport.toUpperCase()} optimization started (${mode} mode)`,
        job_id: result.id || result.job_id,
        mode: mode,
        locked_player: lockedPlayerName,
        excluded_players: excludedPlayers,
        stats: {
          players: stats.playerCount,
          teams: stats.teamCount,
          avgSalary: stats.avgSalary,
          avgProjection: stats.avgProjection
        },
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
