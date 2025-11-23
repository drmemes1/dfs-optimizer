// api/results.js - FIXED VERSION WITH CHAIN FOLLOWING
const https = require('https');

function makeRequest(url, options) {
  return new Promise((resolve, reject) => {
    const urlObj = new URL(url);
    const reqOptions = {
      hostname: urlObj.hostname,
      path: urlObj.pathname + urlObj.search,
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

async function fetchJobWithRetry(jobId, swarmKey, swarmBase) {
  const url = `${swarmBase}/v1/agent-executor-jobs/${jobId}/`;
  
  const response = await makeRequest(url, {
    method: 'GET',
    headers: {
      'Authorization': `Bearer ${swarmKey}`,
      'Content-Type': 'application/json'
    }
  });

  if (response.statusCode !== 200) {
    throw new Error(`Failed to fetch job ${jobId}: status ${response.statusCode}`);
  }

  return JSON.parse(response.body);
}

async function findOptimizerJob(initialJobId, swarmKey, swarmBase, maxDepth = 10) {
  console.log(`\n🔍 Starting chain search from job: ${initialJobId}`);
  
  let currentJobId = initialJobId;
  let depth = 0;

  while (depth < maxDepth) {
    console.log(`\n📍 Depth ${depth}: Checking job ${currentJobId}`);
    
    try {
      const jobData = await fetchJobWithRetry(currentJobId, swarmKey, swarmBase);
      
      console.log(`   Status: ${jobData.status}`);
      console.log(`   Agent: ${jobData.agent_name || 'unknown'}`);
      console.log(`   Has return_value: ${!!jobData.return_value}`);

      const status = jobData.status?.toLowerCase() || 'unknown';

      // If job is still processing, return early
      if (status === 'pending' || status === 'running' || status === 'processing') {
        console.log(`   ⏳ Job still processing at depth ${depth}`);
        return {
          status: 'processing',
          message: `Job processing (chain depth: ${depth})`,
          current_agent: jobData.agent_name || 'unknown'
        };
      }

      // If job failed, return error
      if (status === 'failed') {
        console.log(`   ❌ Job failed at depth ${depth}`);
        return {
          status: 'failed',
          error: jobData.error || 'Job failed in chain',
          failed_agent: jobData.agent_name || 'unknown'
        };
      }

      // Check if this is the OPTIMIZER (has lineup in return_value)
      if (jobData.return_value) {
        let returnValue = jobData.return_value;
        
        // Parse if string
        if (typeof returnValue === 'string') {
          try {
            returnValue = JSON.parse(returnValue);
          } catch (e) {
            console.log(`   ⚠️ Could not parse return_value as JSON`);
          }
        }

        // Check if this is OPTIMIZER output (has lineup)
        if (returnValue && returnValue.lineup && Array.isArray(returnValue.lineup)) {
          console.log(`   ✅ Found OPTIMIZER result with ${returnValue.lineup.length} players!`);
          return {
            status: 'completed',
            data: returnValue,
            final_job_id: currentJobId,
            chain_depth: depth
          };
        }

        // Check for chained job ID
        if (returnValue && returnValue.chain_result) {
          const chainResult = returnValue.chain_result;
          
          // Look for next job ID in chain_result
          const nextJobId = 
            chainResult.response?.id || 
            chainResult.response?.job_id ||
            chainResult.job_id ||
            null;

          if (nextJobId) {
            console.log(`   ➡️ Following chain to next job: ${nextJobId}`);
            currentJobId = nextJobId;
            depth++;
            continue;
          }
        }
      }

      // If we get here, job is complete but not what we want
      console.log(`   ⚠️ Job complete but no lineup or chain found`);
      return {
        status: 'error',
        error: 'Chain ended without finding OPTIMIZER results',
        last_agent: jobData.agent_name || 'unknown',
        depth: depth
      };

    } catch (error) {
      console.error(`   ❌ Error fetching job at depth ${depth}:`, error.message);
      return {
        status: 'error',
        error: error.message,
        depth: depth
      };
    }
  }

  console.log(`   ⚠️ Max chain depth (${maxDepth}) reached`);
  return {
    status: 'error',
    error: `Max chain depth (${maxDepth}) reached without finding results`
  };
}

module.exports = async (req, res) => {
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');

  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  const jobId = req.query.job_id;

  if (!jobId) {
    return res.status(400).json({
      success: false,
      error: 'job_id parameter is required'
    });
  }

  const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
  const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';

  if (!SWARMNODE_KEY) {
    return res.status(500).json({
      success: false,
      error: 'SWARMNODE_API_KEY not configured'
    });
  }

  try {
    console.log(`\n${'='.repeat(60)}`);
    console.log(`RESULTS API: Fetching job ${jobId}`);
    console.log('='.repeat(60));

    const result = await findOptimizerJob(jobId, SWARMNODE_KEY, SWARMNODE_BASE);

    console.log('\n📊 Final result:', result.status);
    console.log('='.repeat(60) + '\n');

    // Handle different result statuses
    if (result.status === 'processing') {
      return res.status(200).json({
        success: true,
        status: 'processing',
        message: result.message,
        current_agent: result.current_agent
      });
    }

    if (result.status === 'failed') {
      return res.status(200).json({
        success: false,
        status: 'failed',
        error: result.error,
        failed_agent: result.failed_agent
      });
    }

    if (result.status === 'completed' && result.data) {
      const data = result.data;
      
      return res.status(200).json({
        success: true,
        status: 'completed',
        lineup: data.lineup || [],
        stats: data.stats || {},
        recommendations: data.recommendations || [],
        locked_player_used: data.locked_player_used || null,
        excluded_players: data.excluded_players || [],
        slate_type: data.slate_type || 'Classic',
        job_id: result.final_job_id,
        chain_depth: result.chain_depth
      });
    }

    // Error case
    return res.status(200).json({
      success: false,
      status: 'error',
      error: result.error || 'Unknown error',
      debug: result
    });

  } catch (error) {
    console.error('❌ Error in results API:', error);
    
    return res.status(500).json({
      success: false,
      status: 'error',
      error: error.message
    });
  }
};
