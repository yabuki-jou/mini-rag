# Archive Agent Project Analysis

## Product purpose

The project archive assistant helps authenticated project staff navigate manually confirmed archives and ask questions
about their original text inside one server-bound project. A successful run selects the correct read-only tool, stays
inside the verified user/project/knowledge-base scope, answers only from sufficient formal evidence, refuses otherwise,
and returns traceable citations without persistent IDs or retrieval scores.

## Users and value

The primary users are archive and project staff who need quick, auditable access to file metadata and source facts.
Unlike a general chatbot, the assistant treats human confirmation, project isolation, refusal, citations, and audit
summaries as server-enforced business rules rather than prompt-only guidance.

## Capability inventory

1. Formal archive catalog navigation with five metadata fields, filters, stable ordering, and pagination.
2. Top-8 source-evidence retrieval followed by the shared D5/D6 sufficiency judgment.
3. Persistent multi-turn conversations with complete user/assistant turn projection.
4. User-visible citations limited to filename, location range, and excerpt.
5. Deterministic refusal when no sufficient formal evidence exists.
6. Server-bound scope isolation and deidentified, idempotent tool-call auditing.

## Realistic inputs

Messages contain 1–2,000 Unicode code points and may use colloquial omissions, line breaks, synonyms, dates, organization
filters, or follow-up references. A project can contain up to 100 TXT, Markdown, PDF, or DOCX files, while only confirmed
and visible files enter the formal catalog and retrieval scope. Evidence may span pages or chunks, and multiple near-match
documents may differ only by version, date, amount, organization, or approval outcome. The frozen evaluation composition
is eight grounded questions, two no-evidence questions, two cross-project isolation questions, two catalog questions, and
two two-turn conversations. Ground truth remains offline evaluation metadata.

## Hard problems and failure modes

1. Wrong tool selection between catalog navigation and source-evidence search.
2. Hallucination from semantically similar but insufficient evidence.
3. Cross-user, cross-project, cross-knowledge-base, or cross-agent-session leakage.
4. Citation drift after repeated tool calls or candidate renumbering.
5. Lost referents or scope drift in a second conversation turn.
6. Leakage of scope IDs, document/chunk IDs, scores, full text, or raw queries in responses and traces.
7. Duplicate visible messages or audit rows after dependency retries.
8. Continued visibility of deleting documents or deleted-project sessions.
