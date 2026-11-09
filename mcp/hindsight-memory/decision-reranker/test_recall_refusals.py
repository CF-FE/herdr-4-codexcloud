"""Synthetic regression against the installed Hindsight recall reranking path."""
import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
import json
import unittest
from unittest.mock import patch

import numpy as np

from hindsight_api.engine.aiohttp_session import UpstreamHTTPError
from hindsight_api.engine.decision_model import DecisionModel, DecisionQuestion, DecisionRefusedError
from hindsight_api.engine.remote_retry import RetryPolicy
from hindsight_api.engine.search.decision_reranker import DecisionModelReranker
from hindsight_api.engine.search.reranking import CrossEncoderReranker
from hindsight_api.engine.search.types import MergedCandidate, RetrievalResult


def predicate(name, probability):
    return {"type": "predicate", "name": name, "probability": probability}


def refusal(name):
    return {"type": "refusal", "name": name}


@dataclass
class Reply:
    status: int
    payload: dict
    headers: dict = field(default_factory=dict)
    url: str = "https://decisions.invalid/v1/decisions"

    async def json(self, *, content_type):
        return self.payload

    async def text(self):
        return json.dumps(self.payload)


class ScriptedSession:
    """Only the external HTTP exchange is scripted."""

    def __init__(self, respond):
        self.respond = respond
        self.requests = []

    def get(self):
        return self

    @asynccontextmanager
    async def post(self, url, *, json):
        if url != "https://decisions.invalid/v1/decisions":
            raise AssertionError("Unexpected request URL")
        self.requests.append(json)
        yield self.respond(json, len(self.requests))


def reranker(respond, *, max_request_tokens=26_000):
    client = DecisionModel(
        provider="openai", api_key="synthetic-key", model="gpt-6-luna",
        base_url="https://decisions.invalid", timeout=1,
        max_concurrent=4, max_request_tokens=max_request_tokens,
        retry_policy=RetryPolicy(max_retries=1, initial_backoff=0, max_backoff=0, budget_seconds=1),
    )
    session = ScriptedSession(respond)
    client._session = session
    return CrossEncoderReranker(DecisionModelReranker(client)), client, session


def candidates(*documents):
    return [
        MergedCandidate(RetrievalResult(str(slot), document, "world"), rrf_score=1 / (slot + 1))
        for slot, document in enumerate(documents)
    ]


def long_candidates():
    return candidates(*(
        " ".join(f"sample {slot} row {row:03d}" for row in range(160)) for slot in range(8)
    ))


class RecallRefusalTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_refusal_is_excluded_and_zero_probability_is_still_an_answer(self):
        ranker, _, session = reranker(lambda body, _: Reply(200, {
            "answers": [predicate("m0", 0), refusal("m1"), predicate("m2", 0.9)],
            "usage": {"input_tokens": 17, "output_tokens": 5},
        }))

        with patch("hindsight_api.engine.decision_model.record_decision_call") as trace:
            result = await ranker.rerank("Which folder is blue?", candidates("first", "second", "third"))

        self.assertEqual([(row.id, row.weight) for row in result.results], [("2", 1.0), ("0", 0.5)])
        self.assertEqual(result.provider_name, "decision_model")
        self.assertEqual(len(session.requests), 1)
        payload = trace.call_args.kwargs["response"]
        self.assertEqual(payload["answers"], {
            "m0": {"noul": 0.0}, "m1": {"refused": True}, "m2": {"noul": 0.9},
        })
        self.assertEqual(payload["usage"], {"input_tokens": 17, "output_tokens": 5})
        self.assertEqual(trace.call_args.kwargs["input_tokens"], 17)

    async def test_pruning_keeps_an_answered_best_when_the_first_candidate_refuses(self):
        ranker, _, session = reranker(lambda body, _: Reply(200, {"answers": [
            refusal("m0"), predicate("m1", 0), predicate("m2", 0),
        ]}))

        result = await ranker.rerank(
            "Which folder is blue?", candidates("first", "second", "third"), prune=True,
        )

        self.assertEqual([(row.id, row.weight) for row in result.results], [("1", 1.0)])
        self.assertEqual(len(session.requests), 1)

    async def test_batch_refusal_does_not_erase_an_answer_in_another_batch(self):
        def respond(body, _):
            return Reply(200, {"answers": [
                predicate(q["name"], 0.7) if q["name"] == "m7" else refusal(q["name"])
                for q in body["questions"]
            ]})

        ranker, _, session = reranker(respond, max_request_tokens=1000)
        result = await ranker.rerank("Which row is relevant?", long_candidates())

        self.assertGreater(len(session.requests), 1)
        self.assertEqual([(row.id, row.weight) for row in result.results], [("7", 1.0)])

    async def test_all_refusals_across_batches_remain_a_failure(self):
        ranker, _, session = reranker(
            lambda body, _: Reply(200, {"answers": [refusal(q["name"]) for q in body["questions"]]}),
            max_request_tokens=1000,
        )

        with self.assertRaises(DecisionRefusedError):
            await ranker.rerank("Which row is relevant?", long_candidates())

        self.assertGreater(len(session.requests), 1)

    async def test_normal_answers_preserve_order_scores_and_tie_breaking(self):
        ranker, _, session = reranker(lambda body, _: Reply(200, {"answers": [
            predicate("m2", 0.2), predicate("m1", 0.8), predicate("m0", 0.2),
        ]}))

        result = await ranker.rerank("Which folder is blue?", candidates("first", "second", "third"))

        self.assertEqual([(row.id, row.weight) for row in result.results], [
            ("1", 1.0), ("0", 2 / 3), ("2", 1 / 3),
        ])
        self.assertEqual(len(session.requests[0]["questions"]), 3)

    async def test_missing_duplicate_unexpected_or_invalid_answers_fail(self):
        examples = {
            "missing name": [predicate("m0", 0.9)],
            "duplicate name": [predicate("m0", 0.9), predicate("m1", 0.1), predicate("m0", 0.8)],
            "unexpected name": [predicate("m0", 0.9), predicate("m1", 0.1), refusal("m2")],
            "wrong type": [predicate("m0", 0.9), {"type": "choice", "name": "m1", "choice": "a"}],
            "missing probability": [predicate("m0", 0.9), {"type": "predicate", "name": "m1"}],
        }
        for description, answers in examples.items():
            with self.subTest(description=description):
                ranker, _, session = reranker(lambda body, _: Reply(200, {"answers": answers}))
                with self.assertRaises(ValueError):
                    await ranker.rerank("Which folder is blue?", candidates("first", "second"))
                self.assertEqual(len(session.requests), 1)

    async def test_dedup_uses_only_surviving_candidates_and_their_original_embeddings(self):
        def respond(body, attempt):
            if attempt == 1:
                return Reply(200, {"answers": [
                    predicate("m0", 0.4), refusal("m1"), predicate("m2", 0.9), predicate("m3", 0.7),
                ]})
            questions = body["questions"]
            self.assertEqual(len(questions), 1)
            instructions = json.loads(questions[0]["instructions"])
            self.assertEqual((instructions["memory_a"], instructions["memory_b"]), ("third", "fourth"))
            return Reply(200, {"answers": [predicate(questions[0]["name"], 0.9)]})

        ranker, _, session = reranker(respond)
        embeddings = asyncio.get_running_loop().create_future()
        embeddings.set_result({
            "0": np.array([0, 1]),
            "1": np.array([1, 0]),
            "2": np.array([1, 0]),
            "3": np.array([1, 0]),
        })

        result = await ranker.rerank(
            "Which folder is blue?", candidates("first", "second", "third", "fourth"),
            dedup=True, embeddings=embeddings,
        )

        self.assertEqual([row.id for row in result.results], ["2", "0"])
        self.assertEqual(len(session.requests), 2)

    async def test_explicit_dedup_refusal_stays_strict(self):
        def respond(body, attempt):
            if attempt == 1:
                return Reply(200, {"answers": [predicate("m0", 0.9), predicate("m1", 0.5)]})
            return Reply(200, {"answers": [refusal(body["questions"][0]["name"])]})

        ranker, _, session = reranker(respond)
        with self.assertRaises(DecisionRefusedError):
            await ranker.rerank("Which folder is blue?", candidates("first", "second"), dedup=True)
        self.assertEqual(len(session.requests), 2)

    async def test_missing_dedup_answer_does_not_assert_distinctness(self):
        def respond(body, attempt):
            if attempt == 1:
                return Reply(200, {"answers": [predicate("m0", 0.9), predicate("m1", 0.5)]})
            return Reply(200, {"answers": []})

        ranker, _, session = reranker(respond)
        with self.assertRaises(ValueError):
            await ranker.rerank("Which folder is blue?", candidates("first", "second"), dedup=True)
        self.assertEqual(len(session.requests), 2)

    async def test_other_decision_consumers_and_generic_predict_stay_strict(self):
        ranker, client, session = reranker(lambda body, _: Reply(200, {"answers": [
            predicate("m0", 0.9), refusal("m1"),
        ]}))
        questions = {
            name: DecisionQuestion(type="noul", instructions="Synthetic comparison")
            for name in ("m0", "m1")
        }

        with self.assertRaises(DecisionRefusedError):
            await client.ask("Synthetic state", questions, scope="decision_consolidation_triage")
        with self.assertRaises(DecisionRefusedError):
            await ranker.cross_encoder.predict([("Which folder?", "first"), ("Which folder?", "second")])
        self.assertEqual(len(session.requests), 2)

    async def test_429_retries_then_returns_the_valid_result(self):
        def respond(body, attempt):
            if attempt == 1:
                return Reply(429, {"error": {"code": "rate_limit"}})
            return Reply(200, {"answers": [predicate("m0", 0.7)]})

        ranker, _, session = reranker(respond)
        result = await ranker.rerank("Which folder is blue?", candidates("first"))

        self.assertEqual([row.id for row in result.results], ["0"])
        self.assertEqual(len(session.requests), 2)

    async def test_exhausted_429_and_nonretryable_401_keep_their_http_status(self):
        for status, attempts in ((429, 2), (401, 1)):
            with self.subTest(status=status):
                ranker, _, session = reranker(lambda body, _: Reply(status, {"error": {"code": "test"}}))
                with self.assertRaises(UpstreamHTTPError) as error:
                    await ranker.rerank("Which folder is blue?", candidates("first"))
                self.assertEqual(error.exception.status_code, status)
                self.assertEqual(len(session.requests), attempts)

    async def test_transport_timeout_remains_a_timeout(self):
        def respond(body, _):
            raise TimeoutError("synthetic upstream timeout")

        ranker, _, session = reranker(respond)
        with self.assertRaises(TimeoutError):
            await ranker.rerank("Which folder is blue?", candidates("first"))
        self.assertEqual(len(session.requests), 2)

    async def test_window_overflow_split_preserves_rank_refusal_policy(self):
        def respond(body, _):
            if len(body["questions"]) > 1:
                return Reply(400, {"detail": {"error_type": "max_tokens_exceeded"}})
            name = body["questions"][0]["name"]
            return Reply(200, {"answers": [refusal(name) if name == "m1" else predicate(name, 0.7)]})

        ranker, _, session = reranker(respond)
        result = await ranker.rerank("Which folder is blue?", candidates("first", "second"))

        self.assertEqual([(row.id, row.weight) for row in result.results], [("0", 1.0)])
        self.assertEqual(len(session.requests), 3)


if __name__ == "__main__":
    unittest.main()
