import { Client } from "@elastic/elasticsearch";

const ELASTICSEARCH_URL = process.env.ELASTICSEARCH_URL!;
const ELASTIC_API_KEY = process.env.ELASTIC_API_KEY!;

let client: Client | null = null;

export function getElasticClient(): Client {
  if (!client) {
    client = new Client({
      node: ELASTICSEARCH_URL,
      auth: { apiKey: ELASTIC_API_KEY },
    });
  }
  return client;
}

// Index names
export const INCIDENTS_INDEX = "ops-incidents";
export const DECISIONS_INDEX = "ops-decisions";
