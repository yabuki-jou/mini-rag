# Archive Agent Entry Point

## Runtime entry

- **Application**: `app/main.py`
- **Local launcher**: `run.py`
- **Type**: authenticated FastAPI HTTP service backed by SQLModel and LangGraph
- **Primary evaluated endpoint**:
  `POST /projects/{project_id}/agent-sessions/{session_id}/messages`

The Pixie Runnable must invoke the FastAPI ASGI interface with a real Bearer token. It may isolate PostgreSQL-compatible
business state and the SQLite checkpoint file, but it must not replace the Archive Graph, tool routing, DeepSeek model,
application service, or response projection.

## HTTP setup and execution flow

1. Register and log in an evaluation-only identity.
2. Create one project; the server creates and binds its internal knowledge base.
3. Create an ARCHIVE session with an empty JSON object.
4. Send one or more user messages through the nested message endpoint.
5. Read message history and deidentified tool-call logs for multi-turn and retry assertions.

```text
Bearer authentication and project ownership
→ ARCHIVE five-field session lookup
→ server-bound Archive Runtime
→ real DeepSeek routing
→ catalog or evidence tool in the verified scope
→ deterministic catalog projection or Top-8 D5/D6 judgment
→ trusted checkpoint AIMessage and deidentified audit
→ ArchiveAgentResponse
```

## User-visible contracts

- Session creation accepts only `{}` and returns public session timestamps and IDs.
- Message creation accepts only a normalized 1–2,000-code-point `message`.
- The response contains answer status, answer text, current citations, and request ID.
- Citations contain only filename, location type/range, and excerpt.
- History excludes tool messages; tool-call history exposes only frozen safe summaries.

## Required configuration

Real evaluation requires a reachable DeepSeek endpoint and API key. A full external retrieval run also requires the
configured PostgreSQL, Chroma Final collection, local BGE embedding model, and local reranker. Authentication needs a JWT
secret, while the evaluation process may set a temporary non-production secret. Checkpoint state must use an isolated
temporary SQLite file. No credential, token, database URL, raw business record, or hidden model reasoning may be recorded.
