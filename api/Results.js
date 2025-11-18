// api/results.js - Fetch OPTIMIZER results from SwarmNode
const https = require('https');

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + (urlObj.search || ''),
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
  // CORS
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }
  
  if (req.method !== 'GET') {
    return res.status(405).json({ success: false, error: 'Method not allowed' });
  }

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3';
    
    // Get job_id from query parameter
    const jobId = req.query.job_id;
    
    if (!jobId) {
      return res.status(400).json({
        success: false,
        error: 'Missing job_id parameter'
      });
    }

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'Server configuration error'
      });
    }

    console.log('Fetching results for job:', jobId);

    // Fetch job status from SwarmNode
    const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
    
    const response = await makeRequest(url, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    console.log('SwarmNode response status:', response.statusCode);

    if (response.statusCode === 404) {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Job not found yet, still processing...'
      });
    }

    // Parse response
    let result;
    try {
      result = JSON.parse(response.body);
    } catch (e) {
      console.error('Failed to parse response:', response.body);
      return res.status(502).json({
        success: false,
        error: 'Could not parse SwarmNode response',
        details: e.message
      });
    }

    console.log('Job result keys:', Object.keys(result));
    console.log('Agent ID in result:', result.agent_id);
    console.log('Job status:', result.status);

    // Check if this is the OPTIMIZER job
    const isOptimizerJob = result.agent_id === OPTIMIZER_AGENT_ID;
    
    // If this is not the optimizer job, check for spawned jobs
    if (!isOptimizerJob) {
      console.log('This is not the OPTIMIZER job, checking spawned jobs...');
      
      // Look for spawned jobs (the pipeline chains through agents)
      const spawnedJobs = result.spawned_jobs || [];
      
      if (spawnedJobs.length > 0) {
        console.log('Found spawned jobs:', spawnedJobs.length);
        
        // Try to fetch the last spawned job (should be OPTIMIZER)
        const lastSpawnedJobId = spawnedJobs[spawnedJobs.length - 1];
        
        try {
          const spawnedUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${lastSpawnedJobId}/`;
          const spawnedResponse = await makeRequest(spawnedUrl, {
            method: 'GET',
            headers: {
              'Authorization': `Bearer ${SWARMNODE_KEY}`,
              'Content-Type': 'application/json'
            }
          });
          
          if (spawnedResponse.statusCode === 200) {
            const spawnedResult = JSON.parse(spawnedResponse.body);
            console.log('Spawned job agent:', spawnedResult.agent_id);
            console.log('Spawned job status:', spawnedResult.status);
            
            if (spawnedResult.agent_id === OPTIMIZER_AGENT_ID) {
              console.log('Found OPTIMIZER job!');
              result = spawnedResult;
            }
          }
        } catch (e) {
          console.error('Error fetching spawned job:', e);
        }
      }
    }

    // Check job status
    const status = result.status || result.state;
    
    console.log('Final job status:', status);

    if (status === 'completed' || status === 'success') {
      // Job is done - extract the lineup from the result
      const output = result.output || result.result || {};
      
      // Check if this output has a lineup (indicates it's the OPTIMIZER)
      if (output.lineup && Array.isArray(output.lineup)) {
        console.log('Found lineup with', output.lineup.length, 'players');
        
        return res.status(200).json({
          success: true,
          status: 'completed',
          lineup: output.lineup,
          stats: output.stats || null,
          locked_player_used: output.locked_player_used || null,
          lineup_export: output.lineup_export || null,
          recommendations: output.recommendations || [],
          raw_output: output
        });
      } else {
        // Completed but no lineup yet (might be earlier agent in chain)
        console.log('Job completed but no lineup found, still waiting for OPTIMIZER');
        return res.status(200).json({
          success: true,
          status: 'processing',
          message: 'Pipeline in progress, waiting for optimizer...'
        });
      }
    } else if (status === 'running' || status === 'pending' || status === 'queued') {
      // Still processing
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: 'Lineup optimization in progress...'
      });
    } else if (status === 'failed' || status === 'error') {
      // Job failed
      const errorMsg = result.error || result.error_message || 'Job failed';
      console.error('Job failed:', errorMsg);
      
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: errorMsg,
        details: result
      });
    } else {
      // Unknown status
      console.log('Unknown status:', status);
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: `Job status: ${status || 'unknown'}`,
        raw_result: result
      });
    }

  } catch (error) {
    console.error('Results endpoint error:', error);
    
    return res.status(500).json({
      success: false,
      error: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
