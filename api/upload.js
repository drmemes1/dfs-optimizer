def main(request):
    # ... (all your existing code above stays the same)
    
    # Sort by value for easier debugging
    players_sorted = sorted(players, key=lambda x: x["value"])

    # ============= ADD THIS SECTION =============
    # Chain to SIGNALS agent
    import requests
    
    SWARMNODE_BASE = os.getenv("SWARMNODE_BASE", "https://api.swarmnode.ai").rstrip("/")
    SWARMNODE_KEY = os.getenv("SWARMNODE_API_KEY", "").strip()
    NEXT_AGENT = os.getenv("NEXT_AGENT_ID_SIGNALS", "").strip()
    
    next_payload = {
        "players": players_sorted,
        "metadata": metadata
    }
    
    chain_result = {"ok": False, "error": "Not attempted"}
    
    if NEXT_AGENT and SWARMNODE_KEY:
        try:
            url = f"{SWARMNODE_BASE}/v1/agent-executor-jobs/create/"
            headers = {
                "Authorization": f"Bearer {SWARMNODE_KEY}",
                "Content-Type": "application/json"
            }
            body = {"agent_id": NEXT_AGENT, "payload": next_payload}
            
            response = requests.post(url, headers=headers, json=body, timeout=30)
            
            if response.status_code in (200, 201):
                chain_result = {"ok": True, "job_id": response.json().get("job_id")}
            else:
                chain_result = {"ok": False, "status": response.status_code}
        except Exception as e:
            chain_result = {"ok": False, "error": str(e)}
    else:
        chain_result = {"ok": False, "error": "Missing NEXT_AGENT_ID_SIGNALS or SWARMNODE_API_KEY"}
    
    print(f"✅ INGEST: Chaining to SIGNALS - {chain_result}")
    # ============= END NEW SECTION =============

    return {
        "ok": True,
        "players": players_sorted,
        "metadata": metadata,
        "validation": validation,
        "summary": {
            "total_players": len(players),
            "avg_salary": validation.get("avg_salary"),
            "positions": validation.get("positions_available"),
            "top_values": [
                {
                    "name": p["name"],
                    "salary": p["salary"],
                    "fppg": p["fppg"],
                    "value": p["value"]
                }
                for p in players_sorted[:5]
            ]
        },
        "chained_to": "SIGNALS",
        "chain_result": chain_result
    }
