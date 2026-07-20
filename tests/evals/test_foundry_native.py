from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import Mock, patch

import httpx
import pytest
from openai import APIConnectionError
from pydantic import ValidationError
from scripts import run_foundry_native_eval

from agent_iq.contracts import (
    FoundryCriterionComparison,
    FoundryEvaluationProtocol,
    FoundryEvaluationSuite,
    FoundryEvaluationTargetMode,
    FoundryEvaluationTooling,
    FoundryInteractionPattern,
    FoundryModelPricingStatus,
    FoundryModelUsage,
)
from agent_iq.evals.foundry_native import (
    AzdEvaluationCapability,
    EvaluationReference,
    EvaluationRunSnapshot,
    FoundryEvaluationCapabilityError,
    FoundryEvaluationResponseError,
    FoundryEvaluationRunError,
    FoundryEvaluationTimeoutError,
    FoundryTraceSelection,
    OfficialFoundryEvaluationApi,
    _as_mapping,
    _case_item,
    _criterion_comparisons,
    _criterion_scores,
    _find_azd,
    _item_schema,
    _model_usage,
    _prune_history,
    _result_counts,
    _run_command,
    _run_duration_ms,
    _run_snapshot,
    _run_status,
    _sdk_operation_error,
    build_trace_data_source,
    load_foundry_evaluation_suite,
    probe_azd_evaluation_capability,
    run_native_foundry_evaluation,
    select_foundry_evaluation_mode,
    tooling_from_capability,
    write_foundry_evaluation_certificate,
)
from agent_iq.evals.rubrics import evaluate_rubric, load_rubric_bundle

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=UTC)


class FakeOfficialRuns:
    def __init__(self) -> None:
        self.output_items = self
        self.created_data_source: object | None = None

    def create(self, **kwargs: object) -> dict[str, object]:
        self.created_data_source = kwargs["data_source"]
        return _sdk_run_payload("in_progress")

    def retrieve(self, **_: object) -> dict[str, object]:
        return _sdk_run_payload("completed")

    def list(self, **_: object) -> list[dict[str, object]]:
        return [{"id": "outputitem_1", "results": []}]


class FakeOfficialEvals:
    def __init__(self) -> None:
        self.runs = FakeOfficialRuns()
        self.created_config: object | None = None
        self.created_criteria: object | None = None
        self.deleted: list[str] = []

    def create(self, **kwargs: object) -> SimpleNamespace:
        self.created_config = kwargs["data_source_config"]
        self.created_criteria = kwargs["testing_criteria"]
        return SimpleNamespace(id="eval_official", name="official")

    def delete(self, *, eval_id: str) -> None:
        self.deleted.append(eval_id)


class FakeOfficialClient:
    def __init__(self) -> None:
        self.evals = FakeOfficialEvals()
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _sdk_run_payload(status: str) -> dict[str, object]:
    return {
        "id": "evalrun_official",
        "status": status,
        "report_url": "https://example.test/report",
        "result_counts": {"total": 1, "passed": 1, "failed": 0, "errored": 0},
        "per_model_usage": [],
        "started_at": NOW,
        "completed_at": NOW + timedelta(seconds=1) if status == "completed" else None,
    }


class FakeEvaluationApi:
    def __init__(
        self,
        suite: FoundryEvaluationSuite,
        *,
        results: list[dict[str, object]] | None = None,
        result_counts: dict[str, int] | None = None,
        terminal_status: str = "completed",
        stay_running: bool = False,
        output_count: int = 1,
    ) -> None:
        self.suite = suite
        self.results = results or _passing_results(suite)
        self.result_counts = result_counts or {
            "total": 1,
            "passed": 1,
            "failed": 0,
            "errored": 0,
        }
        self.terminal_status = terminal_status
        self.stay_running = stay_running
        self.output_count = output_count
        self.created = 0
        self.retrieved = 0
        self.deleted = 0
        self.trace_selection: FoundryTraceSelection | None = None

    def create_evaluation(self, suite: FoundryEvaluationSuite) -> EvaluationReference:
        assert suite is self.suite
        self.created += 1
        return EvaluationReference("eval_native_1", "native")

    def create_run(
        self,
        *,
        evaluation_id: str,
        suite: FoundryEvaluationSuite,
        trace_selection: FoundryTraceSelection | None,
    ) -> EvaluationRunSnapshot:
        assert evaluation_id == "eval_native_1"
        assert suite is self.suite
        self.trace_selection = trace_selection
        return _run("in_progress", self.result_counts)

    def retrieve_run(self, *, evaluation_id: str, run_id: str) -> EvaluationRunSnapshot:
        assert evaluation_id == "eval_native_1"
        assert run_id == "evalrun_native_1"
        self.retrieved += 1
        return _run(
            "in_progress" if self.stay_running else self.terminal_status,
            self.result_counts,
        )

    def list_output_items(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> tuple[dict[str, object], ...]:
        assert evaluation_id == "eval_native_1"
        assert run_id == "evalrun_native_1"
        return tuple(
            {
                "id": f"outputitem_{index}",
                "status": "completed",
                "results": self.results,
                "sample": {
                    "input": [{"content": "sensitive prompt"}],
                    "output": [{"content": "sensitive answer"}],
                },
            }
            for index in range(1, self.output_count + 1)
        )

    def delete_evaluation(self, *, evaluation_id: str) -> None:
        assert evaluation_id == "eval_native_1"
        self.deleted += 1


def _run(status: str, counts: dict[str, int]) -> EvaluationRunSnapshot:
    completed_at = NOW + timedelta(seconds=30) if status == "completed" else None
    return EvaluationRunSnapshot(
        run_id="evalrun_native_1",
        status=status,
        report_url="https://example.test/evaluations/evalrun_native_1",
        result_counts=counts,
        per_model_usage=(
            {
                "run_model_name": "gpt-5.4-mini-2026-03-17",
                "model_name": "gpt-5.4-mini-2026-03-17",
                "invocation_count": 1,
                "prompt_tokens": 900,
                "cached_tokens": 100,
                "completion_tokens": 100,
                "total_tokens": 1000,
            },
            {
                "run_model_name": "azure_ai_evaluation",
                "invocation_count": 1,
                "prompt_tokens": 900,
                "cached_tokens": 100,
                "completion_tokens": 100,
                "total_tokens": 1000,
            },
        ),
        started_at=NOW,
        completed_at=completed_at,
    )


def _passing_results(suite: FoundryEvaluationSuite) -> list[dict[str, object]]:
    scores = {
        "intent-resolution": 4,
        "task-adherence": 1,
        "tool-call-accuracy": 5,
        "response-completeness": 5,
        "groundedness": 5,
        "relevance": 5,
        "retrieval": 5,
        "violence": 0,
        "sexual": 0,
        "self-harm": 0,
        "hate-unfairness": 0,
    }
    return [
        {
            "name": criterion.criterion_id,
            "evaluator_name": criterion.evaluator_name,
            "score": scores[criterion.criterion_id],
            "passed": True,
            "reason": "Private judge rationale that must never be certified.",
        }
        for criterion in suite.criteria
    ]


def _tooling() -> FoundryEvaluationTooling:
    return FoundryEvaluationTooling(
        azure_ai_projects="2.3.0",
        openai="2.45.0",
        azd="1.27.0",
        microsoft_foundry_extension="1.0.0-beta.1",
        azure_ai_agents_extension="1.0.0-beta.5",
    )


def _run_certificate(
    api: FakeEvaluationApi,
    *,
    explanations: dict[str, str] | None = None,
    trace_selection: FoundryTraceSelection | None = None,
) -> Any:
    return run_native_foundry_evaluation(
        api=api,
        suite=api.suite,
        deterministic_report=evaluate_rubric(load_rubric_bundle()),
        tooling=_tooling(),
        divergence_explanations=explanations,
        trace_selection=trace_selection,
        generated_at=NOW,
        poll_interval_seconds=0,
        monotonic=lambda: 0,
        sleeper=lambda _: None,
    )


def test_versioned_foundry_suite_covers_agent_rag_safety_and_budgets() -> None:
    suite = load_foundry_evaluation_suite()

    assert len(suite.criteria) == 11
    assert {criterion.category.value for criterion in suite.criteria} == {
        "agent",
        "rag",
        "safety",
    }
    assert suite.max_duration_ms == 180000
    assert suite.max_total_tokens == 30000
    assert suite.max_estimated_priced_model_cost_usd == 0.05
    assert suite.allow_service_managed_unpriced_usage
    assert suite.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET


def test_native_evaluation_cli_validates_suite_offline(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert run_foundry_native_eval.main(["--validate-only", "--json"]) == 0

    summary = json.loads(capsys.readouterr().out)
    assert summary["passes"] is True
    assert summary["targetMode"] == summary["selectedMode"]
    assert summary["criteria"] > 0
    assert summary["cases"] > 0


@pytest.mark.parametrize(
    ("arguments", "expected_error"),
    [
        ([], "--project-endpoint or FOUNDRY_PROJECT_ENDPOINT is required"),
        (
            ["--project-endpoint", "https://example.services.ai.azure.com/api/projects/test"],
            "--account-name or FOUNDRY_ACCOUNT_NAME is required",
        ),
        (
            [
                "--project-endpoint",
                "https://example.services.ai.azure.com/api/projects/test",
                "--account-name",
                "example",
            ],
            "--project-name or FOUNDRY_PROJECT_NAME is required",
        ),
    ],
)
def test_native_evaluation_cli_requires_independent_target_binding(
    arguments: list[str],
    expected_error: str,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("FOUNDRY_PROJECT_ENDPOINT", raising=False)
    monkeypatch.delenv("FOUNDRY_ACCOUNT_NAME", raising=False)
    monkeypatch.delenv("FOUNDRY_PROJECT_NAME", raising=False)

    with pytest.raises(SystemExit):
        run_foundry_native_eval.main(arguments)

    assert expected_error in capsys.readouterr().err


def test_official_adapter_builds_direct_and_trace_sdk_requests() -> None:
    suite = load_foundry_evaluation_suite()
    client = FakeOfficialClient()
    project = Mock()
    credential = Mock()
    api = object.__new__(OfficialFoundryEvaluationApi)
    api._client = cast(Any, client)
    api._http_client = Mock()
    api._project_client = project
    api._credential = credential
    api._owns_credential = True

    reference = api.create_evaluation(suite)
    created = api.create_run(
        evaluation_id=reference.evaluation_id,
        suite=suite,
        trace_selection=None,
    )
    retrieved = api.retrieve_run(
        evaluation_id=reference.evaluation_id,
        run_id=created.run_id,
    )
    items = api.list_output_items(
        evaluation_id=reference.evaluation_id,
        run_id=created.run_id,
    )
    api.delete_evaluation(evaluation_id=reference.evaluation_id)

    assert reference.evaluation_id == "eval_official"
    assert len(client.evals.created_criteria) == len(suite.criteria)  # type: ignore[arg-type]
    assert retrieved.status == "completed"
    assert items[0]["id"] == "outputitem_1"
    assert client.evals.deleted == ["eval_official"]
    assert client.evals.runs.created_data_source is not None

    trace_payload = suite.model_dump(mode="json", by_alias=True)
    trace_payload.update(
        {
            "targetMode": "trace_replay",
            "protocol": "a2a",
            "interactionPattern": "streaming",
            "cases": [],
        }
    )
    trace_suite = FoundryEvaluationSuite.model_validate(trace_payload)
    trace_reference = api.create_evaluation(trace_suite)
    api.create_run(
        evaluation_id=trace_reference.evaluation_id,
        suite=trace_suite,
        trace_selection=FoundryTraceSelection(trace_ids=("trace-1",)),
    )
    assert client.evals.created_config == {"type": "azure_ai_source", "scenario": "traces"}
    assert client.evals.runs.created_data_source == {
        "type": "azure_ai_traces",
        "trace_ids": ["trace-1"],
        "lookback_hours": 1,
    }
    with pytest.raises(FoundryEvaluationCapabilityError, match="requires a trace"):
        api.create_run(
            evaluation_id=trace_reference.evaluation_id,
            suite=trace_suite,
            trace_selection=None,
        )

    api.close()
    assert client.closed
    project.close.assert_called_once()
    credential.close.assert_called_once()


def test_official_adapter_initializes_context_manager_and_sanitizes_sdk_error() -> None:
    client = FakeOfficialClient()
    project = Mock()
    project.get_openai_client.return_value = client
    credential = Mock()
    with (
        patch("azure.identity.DefaultAzureCredential", return_value=credential),
        patch("azure.ai.projects.AIProjectClient", return_value=project) as project_factory,
        OfficialFoundryEvaluationApi(
            project_endpoint="https://example.services.ai.azure.com/api/projects/test",
            account_name="example",
            project_name="test",
        ) as api,
    ):
        assert cast(Any, api._client) is client
        sdk_kwargs = cast(dict[str, Any], project_factory.call_args.kwargs)
        assert sdk_kwargs["transport"]._use_env_settings is False
        assert sdk_kwargs["redirect_policy"].allow is False
        openai_kwargs = cast(dict[str, Any], project.get_openai_client.call_args.kwargs)
        assert openai_kwargs["http_client"].trust_env is False
        assert openai_kwargs["http_client"].follow_redirects is False
    project.close.assert_called_once()
    credential.close.assert_called_once()

    api = object.__new__(OfficialFoundryEvaluationApi)
    api._client = cast(Any, client)
    api._project_client = project
    api._credential = credential
    api._owns_credential = False
    connection_error = APIConnectionError(request=httpx.Request("POST", "https://example.test"))
    with (
        patch.object(client.evals, "create", side_effect=connection_error),
        pytest.raises(FoundryEvaluationRunError, match="create evaluation"),
    ):
        api.create_evaluation(load_foundry_evaluation_suite())


def test_official_adapter_rejects_untrusted_project_before_credentials() -> None:
    with (
        patch("azure.identity.DefaultAzureCredential") as credential_factory,
        pytest.raises(ValueError, match="does not match"),
    ):
        OfficialFoundryEvaluationApi(
            project_endpoint="https://other.services.ai.azure.com/api/projects/test",
            account_name="example",
            project_name="test",
        )

    credential_factory.assert_not_called()


def test_official_adapter_wraps_run_and_delete_sdk_errors() -> None:
    client = FakeOfficialClient()
    api = object.__new__(OfficialFoundryEvaluationApi)
    api._client = cast(Any, client)
    connection_error = APIConnectionError(request=httpx.Request("GET", "https://example.test"))

    with (
        patch.object(client.evals.runs, "retrieve", side_effect=connection_error),
        pytest.raises(FoundryEvaluationRunError, match="retrieve evaluation run"),
    ):
        api.retrieve_run(evaluation_id="eval", run_id="run")

    with (
        patch.object(client.evals.runs, "create", side_effect=connection_error),
        pytest.raises(FoundryEvaluationRunError, match="create evaluation run"),
    ):
        api.create_run(
            evaluation_id="eval",
            suite=load_foundry_evaluation_suite(),
            trace_selection=None,
        )

    with (
        patch.object(client.evals.runs.output_items, "list", side_effect=connection_error),
        pytest.raises(FoundryEvaluationRunError, match="list evaluation outputs"),
    ):
        api.list_output_items(evaluation_id="eval", run_id="run")

    with (
        patch.object(client.evals, "delete", side_effect=connection_error),
        pytest.raises(FoundryEvaluationRunError, match="delete evaluation"),
    ):
        api.delete_evaluation(evaluation_id="eval")

    class StatusError(Exception):
        status_code = 503

    with pytest.raises(FoundryEvaluationRunError, match=r"HTTP 503"):
        raise _sdk_operation_error("retrieve evaluation run", StatusError())


def test_official_adapter_closes_without_optional_credential_closer() -> None:
    client = FakeOfficialClient()
    project = Mock()
    api = object.__new__(OfficialFoundryEvaluationApi)
    api._client = cast(Any, client)
    api._http_client = Mock()
    api._project_client = project
    api._credential = SimpleNamespace()
    api._owns_credential = True

    api.close()

    assert client.closed
    project.close.assert_called_once()

    second_client = FakeOfficialClient()
    second_project = Mock()
    second_credential = Mock()
    second_api = object.__new__(OfficialFoundryEvaluationApi)
    second_api._client = cast(Any, second_client)
    second_api._http_client = Mock()
    second_api._project_client = second_project
    second_api._credential = second_credential
    second_api._owns_credential = False
    second_api.close()

    second_project.close.assert_called_once()
    second_credential.close.assert_not_called()


def test_direct_target_mode_is_limited_to_supported_execution_shapes() -> None:
    assert (
        select_foundry_evaluation_mode(
            FoundryEvaluationProtocol.RESPONSES,
            FoundryInteractionPattern.SYNCHRONOUS_NON_STREAMING,
        )
        is FoundryEvaluationTargetMode.DIRECT_TARGET
    )
    for protocol, pattern in (
        (FoundryEvaluationProtocol.A2A, FoundryInteractionPattern.SYNCHRONOUS_NON_STREAMING),
        (FoundryEvaluationProtocol.ACTIVITY, FoundryInteractionPattern.DURABLE),
        (FoundryEvaluationProtocol.RESPONSES, FoundryInteractionPattern.STREAMING),
    ):
        assert (
            select_foundry_evaluation_mode(protocol, pattern)
            is FoundryEvaluationTargetMode.TRACE_REPLAY
        )

    payload = load_foundry_evaluation_suite().model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "protocol": "a2a",
            "interactionPattern": "streaming",
            "targetMode": "direct_target",
        }
    )
    with pytest.raises(ValidationError, match="Direct target evaluation only supports"):
        FoundryEvaluationSuite.model_validate(payload)


def test_native_run_rejects_unsupported_direct_target_shape() -> None:
    suite = load_foundry_evaluation_suite()
    unsupported = suite.model_copy(
        update={
            "protocol": FoundryEvaluationProtocol.A2A,
            "interaction_pattern": FoundryInteractionPattern.STREAMING,
        }
    )
    api = FakeEvaluationApi(unsupported)

    with pytest.raises(FoundryEvaluationCapabilityError, match="unsupported execution shape"):
        _run_certificate(api)

    assert api.created == 0


def test_trace_mode_orchestration_requires_and_forwards_bounded_selection() -> None:
    payload = load_foundry_evaluation_suite().model_dump(mode="json", by_alias=True)
    payload.update(
        {
            "targetMode": "trace_replay",
            "protocol": "a2a",
            "interactionPattern": "streaming",
            "cases": [],
        }
    )
    suite = FoundryEvaluationSuite.model_validate(payload)

    with pytest.raises(FoundryEvaluationCapabilityError, match="exactly one"):
        _run_certificate(FakeEvaluationApi(suite))

    selection = FoundryTraceSelection(agent_id="agent:trace", lookback_hours=2, max_traces=3)
    api = FakeEvaluationApi(suite)
    certificate = _run_certificate(api, trace_selection=selection)

    assert certificate.target_mode is FoundryEvaluationTargetMode.TRACE_REPLAY
    assert api.trace_selection == selection


def test_trace_selection_requires_exactly_one_bounded_selector() -> None:
    assert build_trace_data_source(
        FoundryTraceSelection(trace_ids=("trace-1",), lookback_hours=2)
    ) == {
        "type": "azure_ai_traces",
        "trace_ids": ["trace-1"],
        "lookback_hours": 2,
    }
    assert build_trace_data_source(FoundryTraceSelection(agent_id="agent:1", max_traces=20)) == {
        "type": "azure_ai_traces",
        "agent_id": "agent:1",
        "lookback_hours": 1,
        "max_traces": 20,
    }
    with pytest.raises(FoundryEvaluationCapabilityError, match="exactly one"):
        build_trace_data_source(FoundryTraceSelection())
    with pytest.raises(FoundryEvaluationCapabilityError, match="exactly one"):
        build_trace_data_source(FoundryTraceSelection(trace_ids=("trace-1",), agent_id="agent:1"))
    with pytest.raises(FoundryEvaluationCapabilityError, match="must be positive"):
        build_trace_data_source(FoundryTraceSelection(agent_id="agent:1", max_traces=0))


def test_inline_schema_and_usage_parsers_fail_closed() -> None:
    suite = load_foundry_evaluation_suite()
    criterion = suite.criteria[0].model_copy(
        update={"data_mapping": {"query": "{{item.unknown_field}}"}}
    )
    changed = suite.model_copy(update={"criteria": (criterion, *suite.criteria[1:])})
    with pytest.raises(FoundryEvaluationCapabilityError, match="unknown_field"):
        _item_schema(changed)

    usage = _model_usage(
        (
            {
                "run_model_name": "azure_ai_system_model",
                "invocation_count": 1,
                "prompt_tokens": 10,
                "cached_tokens": 0,
                "completion_tokens": 2,
                "total_tokens": 12,
            },
            {
                "run_model_name": "unlisted-model",
                "invocation_count": 1,
                "prompt_tokens": 4,
                "cached_tokens": 0,
                "completion_tokens": 1,
                "total_tokens": 5,
            },
        ),
        suite.model_prices,
    )
    assert [item.pricing_status.value for item in usage] == [
        "service_managed_unpriced",
        "unknown",
    ]
    with pytest.raises(FoundryEvaluationResponseError, match="usage total"):
        _model_usage(
            (
                {
                    "run_model_name": "broken",
                    "invocation_count": 1,
                    "prompt_tokens": 4,
                    "cached_tokens": 0,
                    "completion_tokens": 1,
                    "total_tokens": 99,
                },
            ),
            (),
        )

    snapshot = _run_snapshot(
        SimpleNamespace(
            model_dump=lambda **_: {
                **_sdk_run_payload("completed"),
                "started_at": NOW.isoformat(),
                "completed_at": (NOW + timedelta(seconds=1)).isoformat(),
            }
        )
    )
    assert snapshot.completed_at == NOW + timedelta(seconds=1)


def test_suite_loader_and_native_result_parser_fail_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.json"
    with pytest.raises(ValueError, match="Cannot load Foundry evaluation suite"):
        load_foundry_evaluation_suite(missing)

    malformed = tmp_path / "malformed.json"
    malformed.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ValueError, match="Cannot load Foundry evaluation suite"):
        load_foundry_evaluation_suite(malformed)

    criterion = load_foundry_evaluation_suite().criteria[0]
    malformed_result = _criterion_scores(
        (criterion,),
        ({"results": "not-a-result-list"},),
    )
    assert malformed_result[0].errored_samples == 1
    assert not malformed_result[0].passes

    invalid_pass = _criterion_scores(
        (criterion,),
        ({"results": [{"name": criterion.criterion_id, "passed": "yes", "score": 4}]},),
    )
    assert invalid_pass[0].errored_samples == 1
    assert invalid_pass[0].normalized_score == 0

    fallback_score = _criterion_scores(
        (criterion,),
        ({"results": [{"name": criterion.criterion_id, "passed": True}]},),
    )
    assert fallback_score[0].native_mean_score == 1

    duplicate = {
        "results": [
            {"name": criterion.criterion_id, "passed": True, "score": 1},
            {"name": criterion.criterion_id, "passed": True, "score": 1},
        ]
    }
    with pytest.raises(FoundryEvaluationResponseError, match="more than once"):
        _criterion_scores((criterion,), (duplicate,))

    negative = {"results": [{"name": criterion.criterion_id, "passed": True, "score": -1}]}
    with pytest.raises(FoundryEvaluationResponseError, match="negative score"):
        _criterion_scores((criterion,), (negative,))


def test_native_run_enforces_sample_and_direct_case_limits() -> None:
    suite = load_foundry_evaluation_suite()
    counts = {"total": 2, "passed": 2, "failed": 0, "errored": 0}

    with pytest.raises(FoundryEvaluationResponseError, match="more samples"):
        _run_certificate(
            FakeEvaluationApi(suite, result_counts=counts, output_count=2),
        )

    direct_count_suite = suite.model_copy(update={"max_samples": 2})
    with pytest.raises(FoundryEvaluationResponseError, match="case count"):
        _run_certificate(
            FakeEvaluationApi(direct_count_suite, result_counts=counts, output_count=2),
        )


def test_deterministic_comparison_requires_matching_evidence() -> None:
    suite = load_foundry_evaluation_suite()

    with pytest.raises(FoundryEvaluationResponseError, match="missing comparison criterion"):
        _criterion_comparisons(
            suite.criteria,
            (),
            cast(Any, SimpleNamespace(results=())),
            {},
        )


def test_run_snapshot_status_duration_and_timestamp_boundaries() -> None:
    payload = _sdk_run_payload("completed")
    payload["report_url"] = None
    payload["started_at"] = NOW.isoformat()
    payload["completed_at"] = (NOW + timedelta(seconds=2)).isoformat()
    snapshot = _run_snapshot(payload)

    assert snapshot.report_url is None
    assert _run_status(snapshot.status).value == "completed"
    assert _run_duration_ms(snapshot, fallback_seconds=99) == 2000

    unknown = _sdk_run_payload("completed")
    unknown["status"] = "unexpected"
    with pytest.raises(FoundryEvaluationResponseError, match="unknown run status"):
        _run_status(str(unknown["status"]))

    naive_timestamp = _sdk_run_payload("completed")
    naive_timestamp["started_at"] = NOW.replace(tzinfo=None).isoformat()
    with pytest.raises(FoundryEvaluationResponseError, match="timezone-aware"):
        _run_snapshot(naive_timestamp)

    invalid_timestamp = _sdk_run_payload("completed")
    invalid_timestamp["started_at"] = 123
    with pytest.raises(FoundryEvaluationResponseError, match="optional Foundry timestamp"):
        _run_snapshot(invalid_timestamp)

    invalid_report_url = _sdk_run_payload("completed")
    invalid_report_url["report_url"] = 123
    with pytest.raises(FoundryEvaluationResponseError, match="optional string"):
        _run_snapshot(invalid_report_url)

    backwards = EvaluationRunSnapshot(
        run_id="run",
        status="completed",
        report_url=None,
        result_counts={},
        per_model_usage=(),
        started_at=NOW + timedelta(seconds=1),
        completed_at=NOW,
    )
    with pytest.raises(FoundryEvaluationResponseError, match="precedes its start"):
        _run_duration_ms(backwards, fallback_seconds=0)

    incomplete = EvaluationRunSnapshot(
        run_id="run",
        status="completed",
        report_url=None,
        result_counts={},
        per_model_usage=(),
    )
    assert _run_duration_ms(incomplete, fallback_seconds=1.25) == 1250


@pytest.mark.parametrize(
    "report_url",
    [
        "https://user:secret@example.test/report",  # pragma: allowlist secret
        "https://example.test/report?access_token=secret",
        "https://example.test/report#secret",
        "https://example.test/report\\secret",
        "https://example.test/report/../secret",
        "http://example.test/report",
    ],
)
def test_untrusted_sdk_report_urls_are_omitted(report_url: str) -> None:
    payload = _sdk_run_payload("completed")
    payload["report_url"] = report_url

    assert _run_snapshot(payload).report_url is None


def test_report_url_is_canonicalized_and_contract_sanitizes_direct_input() -> None:
    payload = _sdk_run_payload("completed")
    payload.pop("report_url")
    payload["reportUrl"] = "HTTPS://EXAMPLE.TEST/report/"
    snapshot = _run_snapshot(payload)

    assert snapshot.report_url == "https://example.test/report"

    certificate = _run_certificate(FakeEvaluationApi(load_foundry_evaluation_suite()))
    wire = certificate.model_dump(mode="json", by_alias=True)
    wire["reportUrl"] = "https://user:secret@example.test/report"
    sanitized = type(certificate).model_validate(wire)

    assert sanitized.report_url is None
    assert "secret" not in json.dumps(sanitized.model_dump(mode="json", by_alias=True))


def test_usage_and_response_helpers_handle_aggregate_and_invalid_records() -> None:
    suite = load_foundry_evaluation_suite()
    aggregate = {
        "run_model_name": "azure_ai_evaluation",
        "invocation_count": 1,
        "prompt_tokens": 3,
        "cached_tokens": 1,
        "completion_tokens": 2,
        "total_tokens": 5,
    }
    usage = _model_usage((aggregate,), ())
    assert usage[0].model == "azure_ai_evaluation"
    assert usage[0].pricing_status is FoundryModelPricingStatus.UNKNOWN

    with pytest.raises(FoundryEvaluationResponseError, match="no model name"):
        _model_usage(
            (
                {
                    "invocation_count": 1,
                    "prompt_tokens": 1,
                    "cached_tokens": 0,
                    "completion_tokens": 1,
                    "total_tokens": 2,
                },
            ),
            suite.model_prices,
        )

    with pytest.raises(FoundryEvaluationResponseError, match="non-negative integer"):
        _model_usage(
            (
                {
                    "run_model_name": "broken",
                    "invocation_count": 1,
                    "prompt_tokens": -1,
                    "cached_tokens": 0,
                    "completion_tokens": 1,
                    "total_tokens": 0,
                },
            ),
            suite.model_prices,
        )

    counts = _result_counts(
        {"total": 1, "passed": 1, "failed": 0, "errors": 0},
        ({},),
    )
    assert counts.errors == 0

    assert (
        _case_item({"caseId": "case-1", "query": "query", "toolDefinitions": "malformed"})[
            "tool_definitions"
        ]
        == []
    )
    with pytest.raises(FoundryEvaluationResponseError, match="mapping-compatible"):
        _as_mapping(object())


def test_tooling_and_azd_helpers_are_deterministic(tmp_path: Path) -> None:
    capability = AzdEvaluationCapability(
        azd_version="1.27.0",
        microsoft_foundry_extension="1.0.0",
        azure_ai_agents_extension="0.1.40",
        commands=("generate", "list", "run", "show", "update"),
    )
    with patch(
        "agent_iq.evals.foundry_native.version",
        side_effect=("2.3.0", "2.45.0"),
    ):
        tooling = tooling_from_capability(capability)
    assert tooling.azure_ai_projects == "2.3.0"
    assert tooling.openai == "2.45.0"
    assert tooling.azd == "1.27.0"

    candidate = tmp_path / "Programs" / "Azure Dev CLI" / "azd.exe"
    candidate.parent.mkdir(parents=True)
    candidate.write_text("test", encoding="utf-8")
    with (
        patch("agent_iq.evals.foundry_native.shutil.which", return_value=None),
        patch.dict(os.environ, {"LOCALAPPDATA": str(tmp_path)}, clear=True),
    ):
        assert _find_azd() == str(candidate)

    with patch("agent_iq.evals.foundry_native.shutil.which", return_value="azd-on-path"):
        assert _find_azd() == "azd-on-path"

    empty_root = tmp_path / "empty"
    with (
        patch("agent_iq.evals.foundry_native.shutil.which", return_value=None),
        patch.dict(os.environ, {"LOCALAPPDATA": str(empty_root)}, clear=True),
        pytest.raises(FoundryEvaluationCapabilityError, match="not found"),
    ):
        _find_azd()

    with (
        patch("agent_iq.evals.foundry_native.shutil.which", return_value=None),
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(FoundryEvaluationCapabilityError, match="not found"),
    ):
        _find_azd()

    completed = subprocess.CompletedProcess(("azd", "version"), 0, "{}", "")
    with patch("agent_iq.evals.foundry_native.subprocess.run", return_value=completed) as run:
        assert _run_command(("azd", "version")) is completed
    run.assert_called_once_with(
        ["azd", "version"],
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )

    class InvalidDump:
        def model_dump(self, **_: object) -> list[object]:
            return []

    with pytest.raises(FoundryEvaluationResponseError, match="mapping-compatible"):
        _as_mapping(InvalidDump())

    with pytest.raises(ValueError, match="History limit"):
        _prune_history(tmp_path, 0)


def test_native_run_normalizes_scores_deduplicates_usage_and_passes() -> None:
    suite = load_foundry_evaluation_suite()
    api = FakeEvaluationApi(suite)
    certificate = _run_certificate(api)

    assert certificate.passes
    assert api.deleted == 0
    assert certificate.result_counts.passed == 1
    assert certificate.duration_ms == 30000
    assert certificate.total_tokens == 1000
    assert certificate.estimated_priced_model_cost_usd == pytest.approx(0.0010575)
    assert certificate.priced_model_cost_complete
    assert len(certificate.model_usage) == 1
    intent = next(item for item in certificate.criteria if item.criterion_id == "intent-resolution")
    assert intent.native_mean_score == 4
    assert intent.normalized_score == 0.8
    assert all(item.passes for item in certificate.comparisons)

    serialized = json.dumps(certificate.model_dump(mode="json", by_alias=True))
    assert "sensitive prompt" not in serialized
    assert "sensitive answer" not in serialized
    assert "Private judge rationale" not in serialized


def test_unexplained_score_divergence_fails_but_versioned_code_is_audited() -> None:
    suite = load_foundry_evaluation_suite()
    results = _passing_results(suite)
    next(item for item in results if item["name"] == "intent-resolution")["score"] = 1

    unexplained = _run_certificate(FakeEvaluationApi(suite, results=results))
    comparison = next(
        item for item in unexplained.comparisons if item.foundry_criterion_id == "intent-resolution"
    )
    assert not comparison.passes
    assert not unexplained.passes

    explained = _run_certificate(
        FakeEvaluationApi(suite, results=results),
        explanations={"intent-resolution": "native-scale-calibration-v1"},
    )
    comparison = next(
        item for item in explained.comparisons if item.foundry_criterion_id == "intent-resolution"
    )
    assert comparison.passes
    assert comparison.public_explanation_code == "native-scale-calibration-v1"
    assert explained.passes


def test_native_failure_and_missing_result_fail_closed() -> None:
    suite = load_foundry_evaluation_suite()
    failed_results = _passing_results(suite)
    failed = next(item for item in failed_results if item["name"] == "groundedness")
    failed.update({"score": 1, "passed": False})
    certificate = _run_certificate(
        FakeEvaluationApi(
            suite,
            results=failed_results,
            result_counts={"total": 1, "passed": 0, "failed": 1, "errored": 0},
        )
    )
    assert not certificate.passes
    groundedness = next(
        item for item in certificate.criteria if item.criterion_id == "groundedness"
    )
    assert groundedness.failed_samples == 1

    missing_results = [item for item in _passing_results(suite) if item["name"] != "retrieval"]
    missing = _run_certificate(FakeEvaluationApi(suite, results=missing_results))
    retrieval = next(item for item in missing.criteria if item.criterion_id == "retrieval")
    assert retrieval.errored_samples == 1
    assert not missing.passes


def test_post_run_output_retrieval_failure_cleans_up_the_evaluation() -> None:
    suite = load_foundry_evaluation_suite()

    class FailingOutputApi(FakeEvaluationApi):
        def list_output_items(
            self,
            *,
            evaluation_id: str,
            run_id: str,
        ) -> tuple[dict[str, object], ...]:
            del evaluation_id, run_id
            raise FoundryEvaluationResponseError("malformed output")

    api = FailingOutputApi(suite)
    with pytest.raises(FoundryEvaluationResponseError, match="malformed output"):
        _run_certificate(api)
    assert api.deleted == 1


def test_skipped_native_criterion_is_not_success_shaped() -> None:
    suite = load_foundry_evaluation_suite()
    results = _passing_results(suite)
    tool = next(item for item in results if item["name"] == "tool-call-accuracy")
    tool.update({"passed": None, "score": None, "status": "skipped"})

    certificate = _run_certificate(FakeEvaluationApi(suite, results=results))

    score = next(item for item in certificate.criteria if item.criterion_id == "tool-call-accuracy")
    assert score.skipped_samples == 1
    assert not score.passes
    assert not certificate.passes


@pytest.mark.parametrize("terminal_status", ("failed", "canceled"))
def test_service_count_mismatch_and_terminal_failure_are_classified(
    terminal_status: str,
) -> None:
    suite = load_foundry_evaluation_suite()
    malformed = FakeEvaluationApi(
        suite,
        result_counts={"total": 2, "passed": 2, "failed": 0, "errored": 0},
    )
    with pytest.raises(FoundryEvaluationResponseError, match="result count"):
        _run_certificate(malformed)
    assert malformed.deleted == 1

    terminal = FakeEvaluationApi(suite, terminal_status=terminal_status)
    with pytest.raises(FoundryEvaluationRunError, match=terminal_status):
        _run_certificate(terminal)
    assert terminal.deleted == 1


def test_failed_run_creation_cleans_up_the_evaluation() -> None:
    suite = load_foundry_evaluation_suite()

    class FailingRunApi(FakeEvaluationApi):
        def create_run(
            self,
            *,
            evaluation_id: str,
            suite: FoundryEvaluationSuite,
            trace_selection: FoundryTraceSelection | None,
        ) -> EvaluationRunSnapshot:
            del evaluation_id, suite, trace_selection
            raise FoundryEvaluationRunError("create failed")

    api = FailingRunApi(suite)
    with pytest.raises(FoundryEvaluationRunError, match="create failed"):
        _run_certificate(api)
    assert api.deleted == 1


def test_cleanup_failure_preserves_run_and_cleanup_errors() -> None:
    suite = load_foundry_evaluation_suite()

    class CleanupFailingRunApi(FakeEvaluationApi):
        def create_run(
            self,
            *,
            evaluation_id: str,
            suite: FoundryEvaluationSuite,
            trace_selection: FoundryTraceSelection | None,
        ) -> EvaluationRunSnapshot:
            del evaluation_id, suite, trace_selection
            raise FoundryEvaluationRunError("create failed")

        def delete_evaluation(self, *, evaluation_id: str) -> None:
            super().delete_evaluation(evaluation_id=evaluation_id)
            raise RuntimeError("cleanup failed")

    api = CleanupFailingRunApi(suite)
    with pytest.raises(ExceptionGroup) as raised:
        _run_certificate(api)

    assert api.deleted == 1
    assert len(raised.value.exceptions) == 2
    assert isinstance(raised.value.exceptions[0], FoundryEvaluationRunError)
    assert str(raised.value.exceptions[0]) == "create failed"
    assert str(raised.value.exceptions[1]) == "cleanup failed"
    assert raised.value.__cause__ is raised.value.exceptions[0]


def test_poll_timeout_is_explicit() -> None:
    suite = load_foundry_evaluation_suite()
    ticks = iter((0.0, 2.0))
    api = FakeEvaluationApi(suite, stay_running=True)
    with pytest.raises(FoundryEvaluationTimeoutError, match="exceeded"):
        run_native_foundry_evaluation(
            api=api,
            suite=suite,
            deterministic_report=evaluate_rubric(load_rubric_bundle()),
            tooling=_tooling(),
            poll_interval_seconds=0,
            poll_timeout_seconds=1,
            monotonic=lambda: next(ticks),
            sleeper=lambda _: None,
        )
    assert api.deleted == 1


def test_azd_capability_probe_requires_extensions_and_commands() -> None:
    extensions = [
        {"id": "microsoft.foundry", "installedVersion": "1.0.0-beta.1"},
        {"id": "azure.ai.agents", "installedVersion": "1.0.0-beta.5"},
    ]

    def runner(arguments: Any) -> subprocess.CompletedProcess[str]:
        command = tuple(arguments)
        if command[1] == "version":
            stdout = json.dumps({"azd": {"version": "1.27.0"}})
        elif command[1:3] == ("extension", "list"):
            stdout = json.dumps(extensions)
        else:
            stdout = "\n".join(
                f"  {name}  command" for name in ("generate", "run", "update", "list", "show")
            )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    capability = probe_azd_evaluation_capability(
        azd_executable="azd-test",
        runner=runner,
    )
    assert capability.azd_version == "1.27.0"
    assert capability.azure_ai_agents_extension == "1.0.0-beta.5"
    assert set(capability.commands) == {"generate", "run", "update", "list", "show"}

    extensions.pop()
    with pytest.raises(FoundryEvaluationCapabilityError, match=r"azure\.ai\.agents"):
        probe_azd_evaluation_capability(azd_executable="azd-test", runner=runner)


def test_azd_capability_probe_rejects_malformed_or_old_cli_evidence() -> None:
    valid_extensions = [
        {"id": "microsoft.foundry", "installedVersion": "1.0.0"},
        {"id": "azure.ai.agents", "installedVersion": "0.1.40"},
    ]
    complete_help = "\n".join(
        f"  {name}  command" for name in ("generate", "run", "update", "list", "show")
    )

    def make_runner(
        *,
        version_stdout: str = json.dumps({"azd": {"version": "1.27.0"}}),
        extensions: object = valid_extensions,
        help_stdout: str = complete_help,
        failure: str | None = None,
    ) -> Any:
        def runner(arguments: Any) -> subprocess.CompletedProcess[str]:
            command = tuple(arguments)
            if failure == command[1]:
                return subprocess.CompletedProcess(command, 1, "", "failed")
            if command[1] == "version":
                stdout = version_stdout
            elif command[1:3] == ("extension", "list"):
                stdout = json.dumps(extensions)
            else:
                stdout = help_stdout
            return subprocess.CompletedProcess(command, 0, stdout, "")

        return runner

    with pytest.raises(FoundryEvaluationCapabilityError, match="exit code 1"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(failure="version"),
        )

    with pytest.raises(FoundryEvaluationCapabilityError, match="malformed JSON"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(version_stdout="{not-json"),
        )

    with pytest.raises(FoundryEvaluationCapabilityError, match="not a list"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(extensions={"not": "a list"}),
        )

    with pytest.raises(FoundryEvaluationCapabilityError, match="complete evaluation"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(help_stdout="  generate\n  run\n  update\n  list"),
        )

    with pytest.raises(FoundryEvaluationCapabilityError, match=r"azure\.ai\.agents"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(
                extensions=[
                    None,
                    {"id": 123, "installedVersion": "1.0.0"},
                    {"id": "microsoft.foundry", "installedVersion": "1.0.0"},
                ]
            ),
        )

    with pytest.raises(FoundryEvaluationCapabilityError, match=r"microsoft\.foundry"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(
                extensions=[
                    {"id": "microsoft.foundry", "installedVersion": "0.9.99"},
                    {"id": "azure.ai.agents", "installedVersion": "0.1.40"},
                ]
            ),
        )

    with pytest.raises(FoundryEvaluationCapabilityError, match=r"azure\.ai\.agents"):
        probe_azd_evaluation_capability(
            azd_executable="azd-test",
            runner=make_runner(
                extensions=[
                    {"id": "microsoft.foundry", "installedVersion": "1.0.0"},
                    {"id": "azure.ai.agents", "installedVersion": "0.1.39"},
                ]
            ),
        )


def test_content_safe_certificate_writes_bounded_json_and_html(tmp_path: Path) -> None:
    certificate = _run_certificate(FakeEvaluationApi(load_foundry_evaluation_suite()))
    unrelated_json = tmp_path / "000-unrelated.json"
    unrelated_html = tmp_path / "000-unrelated.html"
    unrelated_directory = tmp_path / "000-directory.json"
    unrelated_json.write_text("keep", encoding="utf-8")
    unrelated_html.write_text("keep", encoding="utf-8")
    unrelated_directory.mkdir()

    first = write_foundry_evaluation_certificate(certificate, tmp_path, history_limit=1)
    later = certificate.model_copy(
        update={
            "certificate_id": "foundry-native-second",
            "generated_at": NOW + timedelta(minutes=1),
        }
    )
    second = write_foundry_evaluation_certificate(later, tmp_path, history_limit=1)

    assert first.json_path != second.json_path
    assert not first.json_path.exists()
    assert not first.html_path.exists()
    assert second.latest_json_path.exists()
    assert unrelated_json.exists()
    assert unrelated_html.exists()
    assert unrelated_directory.exists()
    rendered = second.latest_html_path.read_text(encoding="utf-8")
    assert "Native Foundry evaluation" in rendered
    assert "sensitive prompt" not in rendered
    assert "Private judge rationale" not in rendered


def test_certificate_writes_are_unique_atomic_and_latest_is_replaced(tmp_path: Path) -> None:
    certificate = _run_certificate(FakeEvaluationApi(load_foundry_evaluation_suite()))

    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def record_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        replacements.append((Path(source), Path(target)))
        real_replace(source, target)

    with patch("agent_iq.evals.foundry_native.os.replace", side_effect=record_replace):
        first = write_foundry_evaluation_certificate(certificate, tmp_path, history_limit=2)
        second = write_foundry_evaluation_certificate(certificate, tmp_path, history_limit=2)

    assert first.json_path != second.json_path
    assert first.html_path != second.html_path
    assert first.json_path.exists()
    assert first.html_path.exists()
    assert second.json_path.exists()
    assert second.html_path.exists()
    assert json.loads(second.latest_json_path.read_text(encoding="utf-8")) == json.loads(
        second.json_path.read_text(encoding="utf-8")
    )
    assert second.latest_html_path.read_text(encoding="utf-8") == second.html_path.read_text(
        encoding="utf-8"
    )
    assert len(replacements) == 8
    assert not list(tmp_path.glob(".*.tmp"))


def test_certificate_writer_drops_bypassed_unsafe_report_url(tmp_path: Path) -> None:
    certificate = _run_certificate(FakeEvaluationApi(load_foundry_evaluation_suite()))
    unsafe = certificate.model_copy(
        update={"report_url": "https://user:secret@example.test/report?token=secret"}
    )

    paths = write_foundry_evaluation_certificate(unsafe, tmp_path)
    serialized = paths.json_path.read_text(encoding="utf-8")

    assert '"reportUrl": null' in serialized
    assert "secret" not in serialized


def test_certificate_retention_only_prunes_exact_managed_run_names(tmp_path: Path) -> None:
    certificate = _run_certificate(FakeEvaluationApi(load_foundry_evaluation_suite()))
    unrelated = tmp_path / "agent-iq-foundry-native-20260712T120000Z.json"
    unrelated.write_text("keep", encoding="utf-8")
    unrelated_directory = tmp_path / (
        "agent-iq-foundry-native-20260712T120000000000Z-not-a-uuid.json"
    )
    unrelated_directory.mkdir()

    first = write_foundry_evaluation_certificate(certificate, tmp_path, history_limit=1)
    second = write_foundry_evaluation_certificate(
        certificate.model_copy(update={"generated_at": NOW + timedelta(microseconds=1)}),
        tmp_path,
        history_limit=1,
    )

    assert not first.json_path.exists()
    assert not first.html_path.exists()
    assert second.json_path.exists()
    assert second.html_path.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert unrelated_directory.is_dir()


def test_contracts_reject_tampered_usage_and_comparison() -> None:
    with pytest.raises(ValidationError, match="token total"):
        FoundryModelUsage(
            model="judge",
            invocations=1,
            input_tokens=5,
            cached_input_tokens=1,
            output_tokens=2,
            total_tokens=8,
            pricing_status=FoundryModelPricingStatus.UNKNOWN,
        )
    with pytest.raises(ValidationError, match="delta is inconsistent"):
        FoundryCriterionComparison(
            foundry_criterion_id="native",
            agent_iq_criterion_id="deterministic",
            foundry_score=0.8,
            agent_iq_score=1,
            absolute_delta=0.1,
            maximum_unexplained_delta=0.25,
            passes=True,
        )
