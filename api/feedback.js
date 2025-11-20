// api/feedback.js

function parseLineupCsv(csvText) {
  if (!csvText) return [];

  return csvText
    .split('\n')
    .map(line => line.trim())
    .filter(line => line.length > 0)
    .map(line => {
      const parts = line.split(',');
      const name = parts[0]?.trim();
      const points = parseFloat(parts[1]);
      return {
        name,
        actual_points: isNaN(points) ? null : points
      };
    })
    .filter(p => p.name);
}

export default async function handler(req, res) {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') {
    return res.status(405).json({ ok: false, error: 'Method not allowed' });
  }

  try {
    const {
      optimizer_job_id,
      slate_date,
      my_lineup_csv,
      winning_lineup_csv
    } = req.body || {};

    if (!optimizer_job_id) {
      return res.status(400).json({
        ok: false,
        error: 'Missing optimizer_job_id'
      });
    }

    // Parse the manually entered CSV-style text
    const myLineup = parseLineupCsv(my_lineup_csv);
    const winningLineup = parseLineupCsv(winning_lineup_csv);

    const learnerAgentId = process.env.LEARNER_AGENT_ID;
    const apiKey = process.env.SWARMNODE_API_KEY;

    if (!learnerAgentId || !apiKey) {
      return res.status(500).json({
        ok: false,
        error: 'Missing LEARNER_AGENT_ID or SWARMNODE_API_KEY env vars'
      });
    }

    // Call LEARNER agent on SwarmNode
    const response = await fetch(
      `https://api.swarmnode.ai/v1/agents/${learnerAgentId}/execute/`,
      {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${apiKey}`
        },
        body: JSON.stringify({
          optimizer_job_id,
          slate_date,
          my_lineup: myLineup,
          winning_lineup: winningLineup
        })
      }
    );

    const data = await response.json();

    if (!response.ok) {
      return res.status(500).json({
        ok: false,
        error: data.error || 'Failed to trigger learner agent',
        raw: data
      });
    }

    // Just return whatever the learner agent computed
    return res.status(200).json({
      ok: true,
      learner_response: data
    });

  } catch (err) {
    console.error('Feedback error:', err);
    return res.status(500).json({
      ok: false,
      error: err.message
    });
  }
}
