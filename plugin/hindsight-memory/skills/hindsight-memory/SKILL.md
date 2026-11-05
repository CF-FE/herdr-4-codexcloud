---
name: hindsight-memory
description: Project-shared memory through Hindsight. Use when taking over or resuming work, when prior decisions or experience can resolve a concrete context gap, and when contributing durable project experience with a source. Keep the confirmed project bank, task evidence and native completion state explicit.
metadata:
  version: 1.0.0
---

# Hindsight project memory

Use the `hindsight` MCP tools for the active project's shared memory. The same
confirmed `project_id → bank_id` binding applies across machines, agents and
worktrees. Read the project's memory entry or original task for that binding.
If it is missing or ambiguous, report the gap; do not infer a bank from a local
directory, invent a project identity, or fall back to `default`.

Pass the explicit project `bank_id` on every scoped call. `list_banks` is the only
unscoped discovery tool; use it only to resolve an authorized project binding.
Tool prefixes differ by Harness, so use the available `hindsight` tools rather
than hardcoding a prefix from another client. Bank routing is not project access
authorization.

## Recall when context is needed

Use `recall` when taking over, naturally resuming work, or when an earlier decision,
constraint or failure can answer a specific question. Do not require a lookup on
every turn or load the whole bank.

```text
recall(bank_id: "<confirmed-project-bank>", query: "Which deployment constraint applies to this task, and what is its source?")
```

Ground the answer in returned sources and observation times. If nothing relevant
is found, say so. A historical memory is not the current task state, a live owner
binding or formal acceptance; check the original record when those matter.

Use `reflect` only when deliberate synthesis is needed beyond a factual recall.
It can be slower and uses the configured generation service; it does not replace
the project owner's decision or create authority for a new action.

## Contribute durable experience

Retain useful decisions, effective methods, failure causes and corrections at an
important decision or unit boundary. Ordinary experience can be contributed within
the current task authorization without asking for approval for every record.
Formal rules and business acceptance remain with their original owner and source.

Include project, original task/event, reporter, observation time, source, conclusion
and unconfirmed items using the installed tool's metadata/tags. Store a useful
fact and its source, not the whole transcript. Do not store credentials, unrelated
private data or excluded content, and do not automatically import repository or
conversation history.

Use a stable `document_id` for one source event/revision lineage. Different writers
or independent events need distinct source IDs. Keep an exact-content SHA-256 in
metadata; the service's document hash may be based on cleaned text and is not
automatically the same hash. State the replaced scope when correcting a source.

```text
retain(
  bank_id: "<confirmed-project-bank>",
  document_id: "<stable-source-event-id>",
  content: "<durable conclusion, supporting source and unconfirmed points>",
  metadata: {
    project_id: "<project>", task_ref: "<original-task>", reporter: "<writer>",
    observed_at: "<ISO timestamp with timezone>", source_ref: "<source version>",
    content_sha256: "<hash of the exact submitted text>"
  }
)
```

The installed bridge optionally accepts a caller-allocated UUID `operation_id`
on `retain`, together with `document_id`. For a VPS retry/fallback chain, allocate
it before the first submission and reuse that UUID, bank, document, text and
metadata unchanged. This path uses native Hindsight async replay. Never reuse an
operation UUID for new content. Native MCP retain without this extension generates
its ID at the server; losing that ACK does not make a new UUID safe to submit.

## Completion and weak connections

`accepted`/`SUBMITTED` means an asynchronous operation was admitted, not that
extraction or indexing completed. Preserve the original operation ID and use
`get_operation` when completion matters. Keep pending, failed, cancelled,
`not_found` and `UNKNOWN` distinct. A missing document does not prove no submission
occurred. Do not repeat an uncertain write simply to obtain a success message.

MCP is the first protocol. The VPS service may use the authorized Hindsight API
relay for an MCP discovery/protocol/session gap, with the same project, service
identity and source. It still uses the same Win/EasyTier network. VPS owns the
single bounded attempt budget; do not add another Agent retry loop. Mac LAN keeps
its existing connection behavior.

If recall is unavailable, use the original task input, project source and evidence
to continue steps whose inputs are sufficient. Wait only at decisions missing
necessary evidence. If a write cannot be confirmed, keep its original IDs, hash,
last state and pending summary on the original task for later reconciliation.
Do not create a separate permanent queue, acknowledgement gate or task bus.

## Source and adaptation

Adapted from the official Hindsight `hindsight-memory` skill at commit
`34a138684a899b6ec34175f7a2b633860466d289` (API release 1.2.0):
https://github.com/vectorize-io/hindsight/blob/34a138684a899b6ec34175f7a2b633860466d289/hindsight-integrations/agent-plugin/skills/hindsight-memory/SKILL.md

This independently maintained adaptation follows the confirmed project-memory
contract: explicit bank binding, source-bearing experience, async readback and
nonblocking weak-connection behavior. It includes no conversation collection hooks.
See `SOURCE.json` and `LICENSE` for the fixed source and license.
