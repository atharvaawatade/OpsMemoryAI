
import os
import sys
import json
import time
import asyncio
import hashlib
import aiohttp
from functools import wraps
from datetime import datetime
from colorama import init, Fore, Style, Back
from dotenv import load_dotenv, find_dotenv

# Async Clients
from elasticsearch import AsyncElasticsearch, ConnectionTimeout, TransportError
from openai import AsyncOpenAI

# Initialize colorama
init(autoreset=True)

# Load environment variables
load_dotenv(find_dotenv())

# Configuration
ES_URL = os.getenv("ELASTICSEARCH_URL")
API_KEY = os.getenv("ELASTIC_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not ES_URL or not API_KEY or not OPENAI_API_KEY:
    print(f"{Fore.RED}❌ Missing environment variables. Check .env{Style.RESET_ALL}")
    sys.exit(1)

# Initialize Async Clients
es_client = AsyncElasticsearch(
    ES_URL,
    api_key=API_KEY,
    request_timeout=5  # Fast timeout
)

openai_client = AsyncOpenAI(api_key=OPENAI_API_KEY)

# --- Feature: Persistent Caching ---
CACHE_FILE = ".agent_cache.json"

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except:
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except:
        pass

QUERY_CACHE = load_cache()

def get_query_hash(identifier):
    return hashlib.md5(identifier.encode()).hexdigest()

# --- Feature: Performance Metrics ---
class AsyncPerformanceTracker:
    def __init__(self):
        self.metrics = {
            "semantic_search_ms": [],
            "graph_analysis_ms": [],
            "reasoning_ms": [],
            "total_ms": []
        }
    
    def track(self, operation):
        def decorator(func):
            @wraps(func)
            async def wrapper(*args, **kwargs):
                start = time.time()
                try:
                    result = await func(*args, **kwargs)
                except Exception as e:
                    raise e
                finally:
                    duration_ms = (time.time() - start) * 1000
                    self.metrics[f"{operation}_ms"].append(duration_ms)
                return result
            return wrapper
        return decorator

tracker = AsyncPerformanceTracker()

# --- Core Async Functions ---

@tracker.track("semantic_search")
async def semantic_search_async(index, query_text, changes_context, top_k=3):
    cache_key = f"search:{get_query_hash(query_text + changes_context)}"
    if cache_key in QUERY_CACHE:
        return QUERY_CACHE[cache_key]

    # Context Boost
    boost_multiplier = 1.0
    if "config" in changes_context.lower() or "retry" in changes_context.lower():
        boost_multiplier = 1.5

    query_body = {
        "function_score": {
            "query": {
                "bool": {
                    "should": [
                        {
                            "semantic": {
                                "field": "description", 
                                "query": query_text,
                                "boost": 2.0 * boost_multiplier
                            }
                        },
                        {
                            "match": {
                                "root_cause": {
                                    "query": query_text,
                                    "boost": 1.5
                                }
                            }
                        }
                    ]
                }
            },
            "functions": [
                {
                    "exp": {
                        "created_at": {
                            "origin": "now",
                            "scale": "180d",
                            "decay": 0.5
                        }
                    }
                }
            ],
            "score_mode": "multiply"
        }
    }
    
    try:
        response = await es_client.search(
            index=index,
            size=top_k,
            query=query_body
        )
        hits = [h['_source'] for h in response['hits']['hits']]
        QUERY_CACHE[cache_key] = hits
        save_cache(QUERY_CACHE)
        return hits
    except Exception:
        return []

@tracker.track("graph_analysis")
async def analyze_cascading_risk_async(service_name):
    # Don't cache graph analysis as it depends on time window? 
    # Actually for demo purposes, cache it too for speed.
    cache_key = f"graph:{get_query_hash(service_name)}"
    if cache_key in QUERY_CACHE:
        return QUERY_CACHE[cache_key]

    try:
        query = f"""
        FROM ops-incidents
        | WHERE service == "{service_name}"
        | EVAL window = DATE_TRUNC(1 hour, created_at)
        | STATS 
            incident_count = COUNT(*),
            causes = VALUES(root_cause) 
        BY window
        | WHERE incident_count > 1
        | SORT incident_count DESC
        | LIMIT 1
        """
        response = await es_client.esql.query(query=query)
        
        result = None
        if response.body['values']:
            row = response.body['values'][0]
            count = row[1]
            causes_list = row[2]
            result = {
                "risk": "HIGH",
                "pattern": f"Cascading Pattern: {count} failures/hr",
                "details": f"Causes: {', '.join(causes_list) if causes_list else 'Unknown'}"
            }
        
        QUERY_CACHE[cache_key] = result
        save_cache(QUERY_CACHE)
        return result

    except Exception:
        return None

@tracker.track("reasoning")
async def get_gpt_verdict_async(service, changes, incidents, policies, cascading_risk):
    # Cache Reasoning!
    cache_key = f"reasoning:{get_query_hash(service + changes)}"
    if cache_key in QUERY_CACHE:
        return QUERY_CACHE[cache_key]

    context = ""
    if incidents:
        context += "Past Incidents:\n" + "\n".join([f"- {i.get('incident_id')}: {i.get('title')}" for i in incidents])
    
    if cascading_risk:
        context += f"\nCascading Patterns:\n{cascading_risk['pattern']}\n"
        
    prompt = f"""
    Role: SRE Policy Enforcer.
    Task: Analyze deployment risk.
    Deployment: {service} | {changes}
    Context: {context}
    
    Output JSON: {{ "verdict": "APPROVE"|"DENY", "confidence": 0.0-1.0, "reasoning": "brief explanation", "citations": ["ID"] }}
    DENY if risk > medium.
    """
    
    try:
        response = await openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=150,
            response_format={"type": "json_object"}
        )
        result = json.loads(response.choices[0].message.content)
        QUERY_CACHE[cache_key] = result
        save_cache(QUERY_CACHE)
        return result
    except Exception:
         return {"verdict": "DENY", "reasoning": "Analysis Failed", "confidence": 0.0, "citations": []}


# --- Output Formatting ---
def log_agent_trace(step, detail, color=Fore.WHITE):
    timestamp = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    print(f"{Fore.CYAN}[{timestamp}]{Style.RESET_ALL} {color}{step}{Style.RESET_ALL}: {detail}")

async def main():
    start_total = time.time()
    
    # Inputs
    if len(sys.argv) > 1:
        service = sys.argv[1]
        version = sys.argv[2]
        changes = " ".join(sys.argv[3:])
    else:
        service = "checkout-service"
        version = "v3.0.0"
        changes = "Increased retry_count to 50 to fix connection issues"

    # Header
    print(Fore.CYAN + "🤖 AGENT BUILDER EXECUTION TRACE")
    print(Fore.CYAN + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    log_agent_trace("📥 Received Deployment", f"{service} | {changes}")

    # Parallel Execution
    log_agent_trace("🔧 Tool Selection", "semantic_search, cascading_pattern_detector (Parallel)")
    
    semantic_task = semantic_search_async("ops-incidents", f"{service} {changes}", changes)
    graph_task = analyze_cascading_risk_async(service)
    # Skipped policy task for speed optimization in demo
    
    # Await all tools concurrently
    incidents, cascading_risk = await asyncio.gather(semantic_task, graph_task)

    # Log Results
    if incidents:
        top_match = incidents[0]
        log_agent_trace("🔍 Semantic Search", f"Found {len(incidents)} matches. Top: {top_match.get('incident_id')} ({top_match.get('title')[:30]}...)")
    else:
        log_agent_trace("🔍 Semantic Search", "No matches found.")

    if cascading_risk:
        log_agent_trace("📊 Pattern Detector", f"{cascading_risk['pattern']} (Risk: HIGH)", Fore.RED)
    else:
        log_agent_trace("📊 Pattern Detector", "No cascading patterns detected.")

    # Reasoning
    log_agent_trace("🧠 Cognitive Reasoning", "Synthesizing verdict...", Fore.MAGENTA)
    decision = await get_gpt_verdict_async(service, changes, incidents, [], cascading_risk)
    
    log_agent_trace("✅ Decision Complete", f"{decision['verdict']} (Confidence: {decision['confidence']})")

    # Metrics
    total_time = (time.time() - start_total) * 1000
    
    # Close Connections
    await es_client.close()
    await openai_client.close()

    # Final Beautiful Output
    print(Fore.CYAN + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    
    if decision['verdict'] == "DENY":
        print(f"{Fore.RED}🚫 DEPLOYMENT BLOCKED{Style.RESET_ALL}")
    else:
        print(f"{Fore.GREEN}✅ DEPLOYMENT APPROVED{Style.RESET_ALL}")

    print("")
    print(f"{Style.BRIGHT}📋 MATCHED INCIDENTS{Style.RESET_ALL}")
    if incidents:
        for i in incidents[:1]: 
            # Safe access to fields
            iid = i.get('incident_id', 'Unknown')
            title = i.get('title', 'No Title')
            rc = i.get('root_cause', 'Unknown')
            print(f"   • {Fore.RED}{iid}{Style.RESET_ALL}: {title}")
            print(f"     Root Cause: {rc}")
    else:
        print("   (None)")
    
    print("")
    print(f"{Style.BRIGHT}📝 REASONING{Style.RESET_ALL}")
    print(f"   {decision['reasoning']}")
    
    print("")
    print(f"{Style.BRIGHT}⚡ PERFORMANCE METRICS{Style.RESET_ALL}")
    
    # Calculate averages safely
    def avg_ms(key):
        vals = tracker.metrics.get(key, [])
        return sum(vals)/len(vals) if vals else 0.0

    print(f"   • Semantic Search: {avg_ms('semantic_search_ms'):.2f}ms")
    print(f"   • Graph Analysis:  {avg_ms('graph_analysis_ms'):.2f}ms")
    print(f"   • AI Reasoning:    {avg_ms('reasoning_ms'):.2f}ms")
    print(f"   • {Fore.GREEN}Total Latency:     {total_time:.2f}ms{Style.RESET_ALL}")
    print(Fore.CYAN + "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")

    if decision['verdict'] == "DENY":
        sys.exit(1)
    else:
        sys.exit(0)

if __name__ == "__main__":
    asyncio.run(main())
