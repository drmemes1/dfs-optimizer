// api/optimize.js - Add locked player handling
module.exports = async (req, res) => {
  // ... existing code ...

  try {
    const csvText = req.body?.csv || '';
    const sport = req.body?.sport || 'nba';
    const lockedPlayer = req.body?.locked_player || null; // NEW

    // ... existing validation ...

    console.log(`🚀 Starting ${sport.toUpperCase()} optimization`);
    
    if (lockedPlayer && lockedPlayer.last_name) {
      console.log(`🔒 Locked player: ${lockedPlayer.last_name}${lockedPlayer.max_salary ? ` (max: $${lockedPlayer.max_salary})` : ''}`);
    }

    // Pass locked_player to INGEST
    const payload = {
      csv: csvText,
      sport: sport,
      locked_player: lockedPlayer // NEW
    };

    // ... rest of existing code ...
  }
};
