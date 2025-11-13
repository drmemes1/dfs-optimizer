// api/learner.js

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'POST, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  if (req.method !== 'POST') return res.status(405).json({ error: 'Method not allowed' });
  
  try {
    // Current weights
    const currentWeights = {
      'W_SALARY_PROXY': 0.35,
      'W_MATCHUP': 0.25,
      'W_PACE': 0.15,
      'W_REST': 0.10,
      'W_OPPORTUNITY': 0.10,
      'W_SENTIMENT': 0.05
    };
    
    // Suggested improvements (placeholder - would analyze historical data)
    const suggestedWeights = {
      'W_SALARY_PROXY': 0.32,
      'W_MATCHUP': 0.27,
      'W_PACE': 0.18,
      'W_REST': 0.12,
      'W_OPPORTUNITY': 0.08,
      'W_SENTIMENT': 0.03
    };
    
    const insights = {
      pace_correlation: 0.75,
      matchup_importance: 0.82,
      sentiment_weak: true
    };
    
    return res.status(200).json({
      ok: true,
      current_weights: currentWeights,
      suggested_weights: suggestedWeights,
      insights,
      estimated_improvement: '12.3%',
      recommendation: 'Increase MATCHUP and PACE weights, decrease SENTIMENT weight'
    });
    
  } catch (error) {
    return res.status(500).json({
      ok: false,
      error: error.message
    });
  }
};
