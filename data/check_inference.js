
const { Client } = require("@elastic/elasticsearch");
require("dotenv").config();

const client = new Client({
    node: process.env.ELASTICSEARCH_URL,
    auth: { apiKey: process.env.ELASTIC_API_KEY },
});

async function checkSemanticSearch() {
    try {
        console.log("🔍 Testing semantic search on 'ops-incidents'...");

        // Semantic search query (ELSER)
        const result = await client.search({
            index: "ops-incidents",
            body: {
                query: {
                    semantic: {
                        field: "description",
                        query: "what happens when retries are too high?"
                    }
                }
            }
        });

        const hits = result.hits.hits;
        if (hits.length > 0) {
            console.log(`✅ Success! Found ${hits.length} matches.`);
            console.log(`Top match: ${hits[0]._source.title} (Score: ${hits[0]._score})`);
        } else {
            console.log("⚠️ No matches found. Semantic inference might still be processing. Wait a few minutes.");
        }

    } catch (error) {
        console.error("❌ Error testing semantic search:", error.message);
        if (error.message.includes("semantic")) {
            console.log("💡 Tip: Ensure your index mapping uses 'semantic_text' type correctly.");
        }
    }
}

checkSemanticSearch();
