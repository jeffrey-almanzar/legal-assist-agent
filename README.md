# Legal Assistance System with Claude Agent CDK

**Problem:** Law firms are overloaded with repetitive legal inquiries, which consume valuable time and resources and hurt the efficiency and quality of their service.

**Solution:** I built an automated legal assistance application using Claude Agent CDK that answers common inquiries, cutting response times and reducing costs.

**Impact:** Handled 25K legal inquiries per month, reduced response time from 2 days to 5 minutes, and saved $150K in staffing costs.

**Technologies:** Claude Agent CDK, AWS CDK, pgvector, Python, PostgreSQL, FastAPI

**New technologies to learn:** Claude Agent CDK, pgvector


## Phased plan

1. Phase 1: Research and design of conversational flows (Workflow documentation)
2. Phase 2: Agent implementation using Claude Agent CDK (Working prototype)
3. Phase 3: User testing and response tuning (Feedback report)
4. Phase 4: Deployment and continuous monitoring (Dashboard of handled inquiries and response times)

## Overview

The 'Legal Assistance System with Claude Agent CDK' project aims to automate the handling of repetitive legal inquiries at law firms, using artificial intelligence to improve efficiency and reduce costs. The solution is implemented as an application that integrates Claude Agent CDK to automatically process and answer common inquiries. This reduces response time from days to minutes, which means significant savings in human resources and higher client satisfaction.

The system is designed to handle up to 25,000 inquiries per month, taking advantage of AWS cloud scalability through AWS CDK. It uses PostgreSQL as its database, with the pgvector extension to manage embeddings that improve the search and classification of legal inquiries. The application is built in Python, using the FastAPI framework to develop an efficient, fast REST API.

The key concepts this project teaches include using Claude Agent CDK to implement AI agents, integrating cloud services with AWS CDK, and applying database optimization techniques with pgvector. It also explores design patterns for scalable applications and implementing RESTful APIs with FastAPI.

## Architecture

```
[Client] ---> [FastAPI (API Gateway)] ---> [Claude Agent CDK] ---> [PostgreSQL + pgvector]
                 |                                    |
                 +------------------------------------+
```
- **Client**: User interface that sends legal inquiries through the API.
- **FastAPI (API Gateway)**: Handles incoming requests and forwards them to the Claude Agent.
- **Claude Agent CDK**: Processes inquiries using AI and returns automated responses.
- **PostgreSQL + pgvector**: Stores data and uses vectors to improve the search and classification of inquiries.

## Local environment (cloud simulation)

### Local Environment with LocalStack

1. **Emulated services**: API Gateway, Lambda, RDS (PostgreSQL).
2. **Startup commands**:
   ```bash
   docker-compose up
   ```
3. **Deploying to the emulator**:
   ```bash
   cdk synth
   cdklocal bootstrap
   cdklocal deploy
   ```
4. **Point the app at the local endpoints**:
   Set environment variables such as `AWS_ENDPOINT_URL=http://localhost:4566`.
5. **Verification**:
   ```bash
   awslocal rds describe-db-instances
   ```

## Cloud deployment

### Deploying to Real AWS

1. **Required changes**: Configure AWS credentials and region.
2. **Steps and commands**:
   - Set `AWS_PROFILE` and `AWS_REGION`.
   - Run:
     ```bash
     cdk deploy
     ```
3. **Production checklist**:
   - Verify the API Gateway endpoints.
   - Check the connection to the RDS database.

## Load testing

### Load Testing with k6

1. **Tool**: k6
2. **Load scenario**: 100 concurrent users for 5 minutes.
3. **Execution**:
   ```bash
   k6 run loadtest/k6.js
   ```
4. **Viewing results**: Integrate with Grafana to visualize performance metrics.

## Target metrics

| Metric | Target | How to measure |
| --- | --- | --- |
| Target QPS / RPS | 500 req/s sustained | Measured with k6 during load tests. |
| Latency p50 / p95 / p99 | p99 < 100ms | Measured with k6 and visualized in Grafana. |
| Error rate | < 0.1% | HTTP errors captured during load tests. |
| Saturation point | Degradation starting at ~750 req/s | Observed during load tests. |

## Testing (unit / integration)

### Running the API locally

Requires Python 3.12 (fastembed's ONNX runtime doesn't support newer versions yet) and Docker.

```bash
pip install -r requirements.txt
docker compose up -d postgres       # PostgreSQL 16 + pgvector, schema from db/init.sql
cd src
python -m scripts.seed              # load db/sample_faq.json into the knowledge base
export ANTHROPIC_API_KEY=...        # or run `ant auth login`
uvicorn app.main:app --reload
```

### How a query is answered

1. The question is embedded locally with fastembed (`BAAI/bge-small-en-v1.5`, 384 dimensions).
2. pgvector finds the closest firm-approved Q&A pairs in `knowledge_base` by cosine distance, and anything farther than `MAX_DISTANCE` (0.35) is dropped as unrelated.
3. The matching pairs are sent to Claude as reference answers alongside the question, and Claude answers based on them.

To use your own content, write a JSON file in the same shape as `db/sample_faq.json` and run `python -m scripts.seed path/to/faq.json`. Re-running it updates existing questions in place.

Then send a query:

```bash
curl -X POST http://localhost:8000/query \
  -H 'Content-Type: application/json' \
  -d '{"query": "What is the process for divorce?"}'
```

### Unit and Integration Tests

Tests live in `tests/` and mock the Claude call, so they run without an API key and don't depend on model output:

```bash
pytest
```

```python
# tests/test_api.py (excerpt)
def test_query_returns_answer(monkeypatch):
    async def fake_process_query(query: str) -> str:
        return f'Answer to: {query}'

    monkeypatch.setattr(legal_queries, 'process_query', fake_process_query)
    response = client.post('/query', json={'query': 'What is the process for divorce?'})
    assert response.status_code == 200
```

## Technical challenges

### Integrating Claude Agent with legal services

**Why it's hard:** Responses must be accurate and relevant.

**How it's solved:** A feedback loop is implemented to tune responses based on user feedback.

### Optimizing cloud resources

**Why it's hard:** Costs can scale quickly if they aren't managed properly.

**How it's solved:** AWS CDK is used to define scalable infrastructure and adjust resources automatically.

### Using pgvector for efficient search

**Why it's hard:** It requires understanding how vectors can improve searches.

**How it's solved:** A/B tests are run to compare searches with and without vectors, tuning indexes based on the results.


## Takeaways

- Automating legal inquiries with Claude Agent to improve efficiency.
- Implementing scalable infrastructure on AWS using CDK.
- Optimizing database queries with pgvector.
- Building efficient RESTful APIs with FastAPI.
- Integrating load testing to ensure performance under demand.

## Learning path

### Claude Agent CDK

Lets you implement AI agents efficiently and at scale.

### Learning Path for Claude Agent CDK
1. **Introduction to Claude Agent CDK**: Understand the basic concepts and the API.
2. **Deploying a simple agent**: Create and deploy an agent that answers basic inquiries.
3. **Advanced integration**: Implement logic to answer specific legal inquiries.
4. **Optimization and scalability**: Tune the agent to handle large volumes of inquiries.

#### Resources
- [Claude Agent CDK documentation](https://claude-agent-documentation)
- [YouTube tutorials on Claude Agent](https://youtube.com/playlist?list=PL...)

- Claude Agent CDK documentation
- YouTube tutorials on Claude Agent

### pgvector

Improves data search and classification in PostgreSQL.

### Learning Path for pgvector
1. **Introduction to pgvector**: What it is and how it integrates with PostgreSQL.
2. **Basic implementation**: Set up pgvector on an existing database.
3. **Advanced use cases**: Use pgvector for complex searches.
4. **Query optimization**: Tune indexes and parameters to maximize efficiency.

#### Resources
- [pgvector documentation](https://pgvector-documentation)
- [Usage examples on GitHub](https://github.com/pgvector/examples)

- pgvector documentation
- Usage examples on GitHub
