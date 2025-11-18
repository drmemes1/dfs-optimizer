// api/results-v2.js - Look up optimizer jobs directly
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
  res.setHeader('Access-Control-Allow-Origin', '*');
  res.setHeader('Access-Control-Allow-Methods', 'GET, OPTIONS');
  res.setHeader('Access-Control-Allow-Headers', 'Content-Type');
  
  if (req.method === 'OPTIONS') {
    return res.status(200).end();
  }

  try {
    const SWARMNODE_KEY = process.env.SWARMNODE_API_KEY;
    const SWARMNODE_BASE = process.env.SWARMNODE_BASE || 'https://api.swarmnode.ai';
    const OPTIMIZER_AGENT_ID = process.env.OPTIMIZER_AGENT_ID || '6734a0b0-0555-4975-a1c9-4757ac1d39b3';
    
    const ingestJobId = req.query.job_id;
    
    if (!ingestJobId) {
      return res.status(400).json({
        success: false,
        error: 'Missing job_id parameter'
      });
    }

    if (!SWARMNODE_KEY) {
      return res.status(500).json({
        success: false,
        error: 'SWARMNODE_API_KEY not configured'
      });
    }

    console.log(`\n📋 Looking for OPTIMIZER results for ingest job: ${ingestJobId}`);

    // Strategy 1: Check the ingest job and follow spawned_jobs
    const ingestUrl = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${ingestJobId}/`;
    const ingestResponse = await makeRequest(ingestUrl, {
      method: 'GET',
      headers: {
        'Authorization': `Bearer ${SWARMNODE_KEY}`,
        'Content-Type': 'application/json'
      }
    });

    let ingestJob;
    try {
      ingestJob = JSON.parse(ingestResponse.body);
    } catch (e) {
      console.error('Failed to parse ingest job response');
      return res.status(502).json({
        success: false,
        error: 'Invalid response from SwarmNode'
      });
    }

    console.log('Ingest job status:', ingestJob.status);
    console.log('Spawned jobs:', ingestJob.spawned_jobs);

    // Follow the chain of spawned jobs
    const jobsToCheck = [ingestJobId, ...(ingestJob.spawned_jobs || [])];
    
    console.log(`Checking ${jobsToCheck.length} jobs in the pipeline...`);

    for (const jobId of jobsToCheck) {
      try {
        const url = `${SWARMNODE_BASE}/v1/agent-executor-jobs/${jobId}/`;
        const response = await makeRequest(url, {
          method: 'GET',
          headers: {
            'Authorization': `Bearer ${SWARMNODE_KEY}`,
            'Content-Type': 'application/json'
          }
        });

        if (response.statusCode !== 200) {
          console.log(`Job ${jobId} returned status ${response.statusCode}`);
          continue;
        }

        const job = JSON.parse(response.body);
        console.log(`Job ${jobId}: agent=${job.agent_id}, status=${job.status}`);

        // Check if this is the optimizer (has lineup data)
        const output = job.output || {};
        
        if (output.lineup && Array.isArray(output.lineup) && output.lineup.length > 0) {
          console.log(`✅ Found OPTIMIZER output with ${output.lineup.length} players!`);
          
          return res.status(200).json({
            success: true,
            status: 'completed',
            lineup: output.lineup,
            stats: output.stats || {},
            locked_player_used: output.locked_player_used || null,
            lineup_export: output.lineup_export || null,
            recommendations: output.recommendations || [],
            optimizer_job_id: jobId,
            ingest_job_id: ingestJobId
          });
        }

        // If this job has spawned more jobs, add them to check
        if (job.spawned_jobs && job.spawned_jobs.length > 0) {
          console.log(`Adding ${job.spawned_jobs.length} more spawned jobs to check`);
          jobsToCheck.push(...job.spawned_jobs);
        }

        // Check if job is still running
        if (job.status === 'running' || job.status === 'pending' || job.status === 'queued') {
          console.log('Pipeline still processing...');
        }

        // Check if job failed
        if (job.status === 'failed' || job.status === 'error') {
          console.error('Job failed:', job.error || 'Unknown error');
          return res.status(200).json({
            success: false,
            status: 'failed',
            error: job.error || 'Pipeline job failed'
          });
        }

      } catch (error) {
        console.error(`Error checking job ${jobId}:`, error.message);
        continue;
      }
    }

    // Strategy 2: If we didn't find it, list recent optimizer jobs
    console.log('Lineup not found in spawned jobs, checking recent optimizer jobs...');
    
    const listUrl = `${SWARMNODE_BASE}/v1/agents/${OPTIMIZER_AGENT_ID}/jobs/?limit=10`;
    
    try {
      const listResponse = await makeRequest(listUrl, {
        method: 'GET',
        headers: {
          'Authorization': `Bearer ${SWARMNODE_KEY}`,
          'Content-Type': 'application/json'
        }
      });

      if (listResponse.statusCode === 200) {
        const jobsList = JSON.parse(listResponse.body);
        const recentJobs = jobsList.results || jobsList.jobs || [];
        
        console.log(`Found ${recentJobs.length} recent optimizer jobs`);
        
        // Find the most recent completed job
        const completedJob = recentJobs.find(j => 
          j.status === 'completed' && 
          j.output?.lineup?.length > 0
        );

        if (completedJob) {
          console.log('✅ Found recent completed optimizer job');
          return res.status(200).json({
            success: true,
            status: 'completed',
            lineup: completedJob.output.lineup,
            stats: completedJob.output.stats || {},
            locked_player_used: completedJob.output.locked_player_used || null,
            lineup_export: completedJob.output.lineup_export || null,
            recommendations: completedJob.output.recommendations || [],
            optimizer_job_id: completedJob.id,
            note: 'Found via recent jobs list'
          });
        }
      }
    } catch (error) {
      console.error('Error listing optimizer jobs:', error.message);
    }

    // Still processing
    return res.status(200).json({
      success: true,
      status: 'processing',
      message: 'Pipeline still processing... This can take 20-40 seconds.',
      jobs_checked: jobsToCheck.length
    });

  } catch (error) {
    console.error('❌ Results endpoint error:', error);
    
    return res.status(500).json({
      success: false,
      error: error.message,
      stack: process.env.NODE_ENV === 'development' ? error.stack : undefined
    });
  }
};
