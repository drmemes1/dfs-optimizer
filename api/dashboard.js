// api/dashboard.js
const https = require('https');

// Simple in-memory store (in production, use database)
let performanceData = [];

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname,
      method: options.method || 'GET',
      headers: options.headers || {}
    };
    
    const req = https.request(reqOptions, (res) => {
      let data = '';
      res.on('data', (chunk) => { data += chunk; });
      res.on('end', () => {
        resolve({ statusCode: res.statusCode, body: data });
      });
    });
    
    req.on('error', reject);
    req.end();
  });
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') return res.status(200).end();
  
  try {
    // In production, fetch from database
    // For now, return mock data structure
    
    const dashboardData = {
      ok: true,
      avg_mae: performanceData.length > 0 
        ? performanceData.reduce((sum, d) => sum + d.mae, 0) / performanceData.length 
        : 0,
      accuracy_rate: performanceData.length > 0
        ? performanceData.reduce((sum, d) => sum + d.accuracy, 0) / performanceData.length
        : 0,
      total_slates: performanceData.length,
      last_updated: new Date().toISOString(),
      performance: performanceData.slice(-10).reverse(),
      top_misses: [],
      current_weights: {
        'W_SALARY_PROXY': 0.35,
        'W_MATCHUP': 0.25,
        'W_PACE': 0.15,
        'W_REST': 0.10,
        'W_OPPORTUNITY': 0.10,
        'W_SENTIMENT': 0.05
      }
    };
    
    return res.status(200).json(dashboardData);
    
  } catch (error) {
    return res.status(500).json({ 
      ok: false,
      error: error.message 
    });
  }
};
