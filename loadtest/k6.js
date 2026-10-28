// Load test for POST /query. Run it against a server started with MOCK_LLM=1, so it measures
// this app's own stack (API, embedding, pgvector search, query logging) without paying for
// Claude calls. Use an API key with a high hourly limit, or requests will hit the rate limit:
//
//   cd src && python -m scripts.api_keys create --name "Load test" --role client --hourly-limit 1000000
//   MOCK_LLM=1 uvicorn app.main:app --port 8000
//   k6 run -e API_KEY=jlg_... loadtest/k6.js
//
// Afterwards, remove the mock rows from the log:  DELETE FROM query_log WHERE model = 'mock';
import http from 'k6/http';
import { check, fail } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';
// Realistic questions: the same set the retrieval eval uses.
const QUERIES = JSON.parse(open('../evals/retrieval_cases.json')).map((c) => c.query);

export const options = {
  stages: [
    { duration: '20s', target: 10 },
    { duration: '20s', target: 50 },
    { duration: '60s', target: 50 },
    { duration: '10s', target: 0 },
  ],
  thresholds: {
    http_req_failed: ['rate<0.01'],
    http_req_duration: ['p(95)<1000'],
  },
};

export function setup() {
  const health = http.get(`${BASE_URL}/health`).json();
  // Refuse to run against a server that would call Claude for every request.
  if (!health.mock_llm) fail('Server is not running with MOCK_LLM=1; refusing to send paid traffic.');
  if (!__ENV.API_KEY) fail('Set -e API_KEY=... to a key with a high hourly limit.');
}

export default function () {
  const query = QUERIES[Math.floor(Math.random() * QUERIES.length)];
  const res = http.post(`${BASE_URL}/query`, JSON.stringify({ query }), {
    headers: { 'Content-Type': 'application/json', 'X-API-Key': __ENV.API_KEY },
  });
  check(res, { 'status 200': (r) => r.status === 200 });
}
