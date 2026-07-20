"""Native Microsoft Foundry evaluation adapter and content-safe certification."""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import uuid
import warnings
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, Protocol

from azure.core.credentials import TokenCredential

from agent_iq.config import canonicalize_foundry_project_endpoint
from agent_iq.connectors.entra import isolated_default_azure_credential
from agent_iq.contracts import (
    FoundryCriterionComparison,
    FoundryCriterionDefinition,
    FoundryCriterionScore,
    FoundryEvaluationProtocol,
    FoundryEvaluationResultCounts,
    FoundryEvaluationRunStatus,
    FoundryEvaluationSuite,
    FoundryEvaluationTargetMode,
    FoundryEvaluationTooling,
    FoundryInteractionPattern,
    FoundryJudgeParameter,
    FoundryModelPrice,
    FoundryModelPricingStatus,
    FoundryModelUsage,
    FoundryNativeEvaluationCertificate,
    RubricEvaluationReport,
)
from agent_iq.contracts.evaluation import _canonical_report_url

DEFAULT_SUITE_PATH = Path(__file__).resolve().parents[3] / "evals" / "foundry_native_suite.json"
DEFAULT_REPORT_ROOT = Path(__file__).resolve().parents[3] / "reports" / "evals" / "foundry-native"
DEFAULT_HISTORY_LIMIT = 25
_TERMINAL_STATUSES = {"completed", "failed", "canceled"}
_AGGREGATE_USAGE_ALIASES = {"azure_ai_evaluation"}
_SERVICE_MANAGED_USAGE_ALIASES = {"azure_ai_system_model"}
_ITEM_FIELD_RE = re.compile(r"^\{\{item\.([A-Za-z0-9_]+)\}\}$")
_CERTIFICATE_RUN_PREFIX = "agent-iq-foundry-native-"
_CERTIFICATE_HISTORY_NAME_RE = re.compile(
    rf"^{re.escape(_CERTIFICATE_RUN_PREFIX)}[0-9]{{8}}T[0-9]{{12}}Z-[0-9a-f]{{32}}\.(?:json|html)$"
)


class FoundryEvaluationError(RuntimeError):
    """Base error for native Foundry evaluation failures."""


class FoundryEvaluationCapabilityError(FoundryEvaluationError):
    """Required SDK or azd evaluation capability is unavailable."""


class FoundryEvaluationTimeoutError(FoundryEvaluationError):
    """The native evaluation did not reach a terminal state in time."""


class FoundryEvaluationRunError(FoundryEvaluationError):
    """The native evaluation service returned a failed or canceled run."""


class FoundryEvaluationResponseError(FoundryEvaluationError):
    """The evaluation service returned malformed or inconsistent evidence."""


@dataclass(frozen=True, slots=True)
class EvaluationReference:
    evaluation_id: str
    name: str


@dataclass(frozen=True, slots=True)
class EvaluationRunSnapshot:
    run_id: str
    status: str
    report_url: str | None
    result_counts: Mapping[str, int]
    per_model_usage: tuple[Mapping[str, object], ...]
    started_at: datetime | None = None
    completed_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class FoundryTraceSelection:
    """Runtime-only trace selection; raw trace data is never put in the certificate."""

    trace_ids: tuple[str, ...] = ()
    agent_id: str | None = None
    lookback_hours: int = 1
    max_traces: int = 50


@dataclass(frozen=True, slots=True)
class AzdEvaluationCapability:
    azd_version: str
    microsoft_foundry_extension: str
    azure_ai_agents_extension: str
    commands: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FoundryCertificatePaths:
    json_path: Path
    html_path: Path
    latest_json_path: Path
    latest_html_path: Path


class FoundryEvaluationApi(Protocol):
    """Minimal provider boundary around the official Foundry/OpenAI evaluation SDK."""

    def create_evaluation(self, suite: FoundryEvaluationSuite) -> EvaluationReference: ...

    def create_run(
        self,
        *,
        evaluation_id: str,
        suite: FoundryEvaluationSuite,
        trace_selection: FoundryTraceSelection | None,
    ) -> EvaluationRunSnapshot: ...

    def retrieve_run(self, *, evaluation_id: str, run_id: str) -> EvaluationRunSnapshot: ...

    def list_output_items(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> tuple[Mapping[str, object], ...]: ...

    def delete_evaluation(self, *, evaluation_id: str) -> None: ...


class OfficialFoundryEvaluationApi:
    """Official ``azure-ai-projects`` and OpenAI client implementation."""

    def __init__(
        self,
        *,
        project_endpoint: str,
        account_name: str,
        project_name: str,
        credential: TokenCredential | None = None,
    ) -> None:
        from azure.ai.projects import AIProjectClient
        from azure.core.pipeline.policies import RedirectPolicy
        from azure.core.pipeline.transport import RequestsTransport
        from azure.identity import DefaultAzureCredential
        from openai import DefaultHttpxClient

        endpoint = canonicalize_foundry_project_endpoint(
            project_endpoint,
            expected_account=account_name,
            expected_project=project_name,
        )
        self._owns_credential = credential is None
        self._credential = credential or isolated_default_azure_credential(
            credential_factory=DefaultAzureCredential,
        )
        self._http_client = DefaultHttpxClient(follow_redirects=False, trust_env=False)
        self._project_client = AIProjectClient(
            endpoint=endpoint,
            credential=self._credential,
            transport=RequestsTransport(use_env_settings=False),
            redirect_policy=RedirectPolicy(permit_redirects=False),
        )
        self._client = self._project_client.get_openai_client(
            http_client=self._http_client,
        )

    def __enter__(self) -> OfficialFoundryEvaluationApi:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()
        self._http_client.close()
        self._project_client.close()
        if self._owns_credential:
            close = getattr(self._credential, "close", None)
            if callable(close):
                close()

    def create_evaluation(self, suite: FoundryEvaluationSuite) -> EvaluationReference:
        from azure.ai.projects.models import TestingCriterionAzureAIEvaluator
        from openai import APIError
        from openai.types.eval_create_params import DataSourceConfigCustom

        criteria = []
        for criterion in suite.criteria:
            if criterion.judge_parameter is FoundryJudgeParameter.NONE:
                configured = TestingCriterionAzureAIEvaluator(
                    type="azure_ai_evaluator",
                    name=criterion.criterion_id,
                    evaluator_name=criterion.evaluator_name,
                    data_mapping=criterion.data_mapping,
                )
            else:
                configured = TestingCriterionAzureAIEvaluator(
                    type="azure_ai_evaluator",
                    name=criterion.criterion_id,
                    evaluator_name=criterion.evaluator_name,
                    initialization_parameters={criterion.judge_parameter.value: suite.judge_model},
                    data_mapping=criterion.data_mapping,
                )
            criteria.append(configured)

        if suite.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET:
            data_source_config: object = DataSourceConfigCustom(
                type="custom",
                item_schema=_item_schema(suite),
                include_sample_schema=True,
            )
        else:
            data_source_config = {"type": "azure_ai_source", "scenario": "traces"}

        try:
            created = self._client.evals.create(
                name=f"{suite.suite_id}-{suite.suite_version}",
                data_source_config=data_source_config,  # type: ignore[arg-type]
                testing_criteria=criteria,
            )
        except APIError as exc:
            raise _sdk_operation_error("create evaluation", exc) from exc
        return EvaluationReference(evaluation_id=created.id, name=created.name)

    def create_run(
        self,
        *,
        evaluation_id: str,
        suite: FoundryEvaluationSuite,
        trace_selection: FoundryTraceSelection | None,
    ) -> EvaluationRunSnapshot:
        from openai import APIError

        if suite.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET:
            data_source = _direct_target_data_source(suite)
        else:
            if trace_selection is None:
                raise FoundryEvaluationCapabilityError("Trace replay requires a trace selection.")
            data_source = build_trace_data_source(trace_selection)

        try:
            # The Foundry target extension is newer than the OpenAI client's union stub.
            with warnings.catch_warnings():
                warnings.filterwarnings(
                    "ignore",
                    message=r"^Pydantic serializer warnings:",
                    category=UserWarning,
                )
                created = self._client.evals.runs.create(
                    eval_id=evaluation_id,
                    name=_new_foundry_run_name(suite.suite_id),
                    metadata={
                        "suite_id": suite.suite_id,
                        "suite_version": suite.suite_version,
                        "target_mode": suite.target_mode.value,
                    },
                    data_source=data_source,  # type: ignore[arg-type]
                )
        except APIError as exc:
            raise _sdk_operation_error("create evaluation run", exc) from exc
        return _run_snapshot(created)

    def retrieve_run(self, *, evaluation_id: str, run_id: str) -> EvaluationRunSnapshot:
        from openai import APIError

        try:
            run = self._client.evals.runs.retrieve(eval_id=evaluation_id, run_id=run_id)
        except APIError as exc:
            raise _sdk_operation_error("retrieve evaluation run", exc) from exc
        return _run_snapshot(run)

    def list_output_items(
        self,
        *,
        evaluation_id: str,
        run_id: str,
    ) -> tuple[Mapping[str, object], ...]:
        from openai import APIError

        try:
            items = self._client.evals.runs.output_items.list(
                eval_id=evaluation_id,
                run_id=run_id,
            )
            return tuple(_as_mapping(item) for item in items)
        except APIError as exc:
            raise _sdk_operation_error("list evaluation outputs", exc) from exc

    def delete_evaluation(self, *, evaluation_id: str) -> None:
        from openai import APIError

        try:
            self._client.evals.delete(eval_id=evaluation_id)
        except APIError as exc:
            raise _sdk_operation_error("delete evaluation", exc) from exc


def load_foundry_evaluation_suite(
    path: Path = DEFAULT_SUITE_PATH,
) -> FoundryEvaluationSuite:
    """Load one strict, versioned native Foundry evaluation suite."""

    try:
        return FoundryEvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"Cannot load Foundry evaluation suite at {path}: {exc}") from exc


def select_foundry_evaluation_mode(
    protocol: FoundryEvaluationProtocol,
    interaction_pattern: FoundryInteractionPattern,
) -> FoundryEvaluationTargetMode:
    """Use direct target only for the execution shapes Foundry supports."""

    if (
        protocol
        in {
            FoundryEvaluationProtocol.RESPONSES,
            FoundryEvaluationProtocol.INVOCATIONS,
        }
        and interaction_pattern is FoundryInteractionPattern.SYNCHRONOUS_NON_STREAMING
    ):
        return FoundryEvaluationTargetMode.DIRECT_TARGET
    return FoundryEvaluationTargetMode.TRACE_REPLAY


def build_trace_data_source(selection: FoundryTraceSelection) -> dict[str, object]:
    """Create a fail-closed native trace data source."""

    if bool(selection.trace_ids) == bool(selection.agent_id):
        raise FoundryEvaluationCapabilityError(
            "Provide exactly one of trace_ids or agent_id for trace replay."
        )
    if selection.lookback_hours <= 0 or selection.max_traces <= 0:
        raise FoundryEvaluationCapabilityError(
            "Trace lookback and maximum trace count must be positive."
        )
    if selection.trace_ids:
        return {
            "type": "azure_ai_traces",
            "trace_ids": list(selection.trace_ids),
            "lookback_hours": selection.lookback_hours,
        }
    return {
        "type": "azure_ai_traces",
        "agent_id": selection.agent_id,
        "lookback_hours": selection.lookback_hours,
        "max_traces": selection.max_traces,
    }


def probe_azd_evaluation_capability(
    *,
    azd_executable: str | None = None,
    runner: Callable[[Sequence[str]], subprocess.CompletedProcess[str]] | None = None,
) -> AzdEvaluationCapability:
    """Require the installed azd evaluation extensions and command group."""

    executable = azd_executable or _find_azd()
    run = runner or _run_command
    version_result = run((executable, "version", "--output", "json"))
    extension_result = run((executable, "extension", "list", "--output", "json"))
    help_result = run((executable, "ai", "agent", "eval", "--help"))
    for label, result in (
        ("azd version", version_result),
        ("azd extension list", extension_result),
        ("azd ai agent eval", help_result),
    ):
        if result.returncode != 0:
            raise FoundryEvaluationCapabilityError(
                f"{label} failed with exit code {result.returncode}."
            )

    try:
        azd_version = str(json.loads(version_result.stdout)["azd"]["version"])
        extensions = json.loads(extension_result.stdout)
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise FoundryEvaluationCapabilityError("azd returned malformed JSON.") from exc
    if not isinstance(extensions, list):
        raise FoundryEvaluationCapabilityError("azd extension inventory is not a list.")

    installed: dict[str, str] = {}
    for item in extensions:
        if isinstance(item, dict):
            identifier = item.get("id")
            installed_version = item.get("installedVersion")
            if (
                isinstance(identifier, str)
                and isinstance(installed_version, str)
                and installed_version
            ):
                installed[identifier] = installed_version
    foundry_version = installed.get("microsoft.foundry")
    agents_version = installed.get("azure.ai.agents")
    if foundry_version is None or _numeric_version(foundry_version) < (1, 0, 0):
        raise FoundryEvaluationCapabilityError(
            "microsoft.foundry extension 1.0.0 or newer is required."
        )
    if agents_version is None or _numeric_version(agents_version) < (0, 1, 40):
        raise FoundryEvaluationCapabilityError(
            "azure.ai.agents extension 0.1.40 or newer is required."
        )

    commands = tuple(
        sorted(
            {
                match.group(1)
                for line in help_result.stdout.splitlines()
                if (match := re.match(r"^\s+(generate|run|update|list|show)\b", line))
            }
        )
    )
    required = {"generate", "run", "update", "list", "show"}
    if not required.issubset(commands):
        raise FoundryEvaluationCapabilityError(
            "azd ai agent eval does not advertise the complete evaluation command set."
        )
    return AzdEvaluationCapability(
        azd_version=azd_version,
        microsoft_foundry_extension=foundry_version,
        azure_ai_agents_extension=agents_version,
        commands=commands,
    )


def tooling_from_capability(capability: AzdEvaluationCapability) -> FoundryEvaluationTooling:
    """Bind SDK package versions to an observed azd capability probe."""

    return FoundryEvaluationTooling(
        azure_ai_projects=version("azure-ai-projects"),
        openai=version("openai"),
        azd=capability.azd_version,
        microsoft_foundry_extension=capability.microsoft_foundry_extension,
        azure_ai_agents_extension=capability.azure_ai_agents_extension,
    )


def run_native_foundry_evaluation(
    *,
    api: FoundryEvaluationApi,
    suite: FoundryEvaluationSuite,
    deterministic_report: RubricEvaluationReport,
    tooling: FoundryEvaluationTooling,
    trace_selection: FoundryTraceSelection | None = None,
    divergence_explanations: Mapping[str, str] | None = None,
    generated_at: datetime | None = None,
    poll_interval_seconds: float = 5,
    poll_timeout_seconds: float | None = None,
    monotonic: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], None] = time.sleep,
) -> FoundryNativeEvaluationCertificate:
    """Run, normalize, compare, and certify one native Foundry evaluation."""

    selected_mode = select_foundry_evaluation_mode(
        suite.protocol,
        suite.interaction_pattern,
    )
    if (
        suite.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET
        and selected_mode is not FoundryEvaluationTargetMode.DIRECT_TARGET
    ):
        raise FoundryEvaluationCapabilityError(
            "The suite requests direct target evaluation for an unsupported execution shape."
        )
    if suite.target_mode is FoundryEvaluationTargetMode.TRACE_REPLAY:
        build_trace_data_source(
            trace_selection if trace_selection is not None else FoundryTraceSelection()
        )

    started = monotonic()
    evaluation = api.create_evaluation(suite)
    try:
        run = api.create_run(
            evaluation_id=evaluation.evaluation_id,
            suite=suite,
            trace_selection=trace_selection,
        )
        timeout = poll_timeout_seconds or (suite.max_duration_ms / 1000) + 60
        while run.status not in _TERMINAL_STATUSES:
            if monotonic() - started > timeout:
                raise FoundryEvaluationTimeoutError(
                    f"Foundry evaluation run {run.run_id} exceeded {timeout:.0f} seconds."
                )
            sleeper(poll_interval_seconds)
            run = api.retrieve_run(
                evaluation_id=evaluation.evaluation_id,
                run_id=run.run_id,
            )
        status = _run_status(run.status)
        if status is not FoundryEvaluationRunStatus.COMPLETED:
            raise FoundryEvaluationRunError(
                f"Foundry evaluation run {run.run_id} ended with status {status.value}."
            )

        output_items = api.list_output_items(
            evaluation_id=evaluation.evaluation_id,
            run_id=run.run_id,
        )
        result_counts = _result_counts(run.result_counts, output_items)
        if result_counts.total > suite.max_samples:
            raise FoundryEvaluationResponseError(
                "Foundry evaluation returned more samples than the suite permits."
            )
        if (
            suite.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET
            and result_counts.total != len(suite.cases)
        ):
            raise FoundryEvaluationResponseError(
                "Direct target result count does not match the inline case count."
            )
        criterion_scores = _criterion_scores(suite.criteria, output_items)
        comparisons = _criterion_comparisons(
            suite.criteria,
            criterion_scores,
            deterministic_report,
            divergence_explanations or {},
        )
        usage = _model_usage(run.per_model_usage, suite.model_prices)
        total_tokens = sum(item.total_tokens for item in usage)
        known_costs = [
            item.estimated_cost_usd for item in usage if item.estimated_cost_usd is not None
        ]
        estimated_cost = sum(known_costs) if known_costs else None
        cost_complete = bool(usage) and len(known_costs) == len(usage)
        duration_ms = _run_duration_ms(run, fallback_seconds=monotonic() - started)
        unpriced_usage = [
            item for item in usage if item.pricing_status is not FoundryModelPricingStatus.PRICED
        ]
        allowed_unpriced_usage = not unpriced_usage or (
            suite.allow_service_managed_unpriced_usage
            and all(
                item.pricing_status is FoundryModelPricingStatus.SERVICE_MANAGED_UNPRICED
                for item in unpriced_usage
            )
        )
        within_cost = suite.max_estimated_priced_model_cost_usd is None or (
            estimated_cost is not None
            and estimated_cost <= suite.max_estimated_priced_model_cost_usd
        )
        passes = (
            result_counts.failed == 0
            and result_counts.errors == 0
            and all(item.passes for item in criterion_scores)
            and all(item.passes for item in comparisons)
            and duration_ms <= suite.max_duration_ms
            and total_tokens <= suite.max_total_tokens
            and within_cost
            and allowed_unpriced_usage
        )
        return FoundryNativeEvaluationCertificate(
            certificate_id=f"foundry-native-{run.run_id}",
            generated_at=generated_at or datetime.now(tz=UTC),
            suite_id=suite.suite_id,
            suite_version=suite.suite_version,
            suite_sha256=_suite_hash(suite),
            target_mode=suite.target_mode,
            protocol=suite.protocol,
            interaction_pattern=suite.interaction_pattern,
            target_agent_name=suite.target_agent_name,
            target_agent_version=suite.target_agent_version,
            judge_model=suite.judge_model,
            evaluation_id=evaluation.evaluation_id,
            run_id=run.run_id,
            run_status=status,
            report_url=run.report_url,
            tooling=tooling,
            result_counts=result_counts,
            criteria=criterion_scores,
            comparisons=comparisons,
            model_usage=usage,
            duration_ms=duration_ms,
            max_samples=suite.max_samples,
            max_duration_ms=suite.max_duration_ms,
            total_tokens=total_tokens,
            max_total_tokens=suite.max_total_tokens,
            estimated_priced_model_cost_usd=estimated_cost,
            max_estimated_priced_model_cost_usd=suite.max_estimated_priced_model_cost_usd,
            priced_model_cost_complete=cost_complete,
            allow_service_managed_unpriced_usage=(suite.allow_service_managed_unpriced_usage),
            passes=passes,
        )
    except Exception as original_error:
        try:
            api.delete_evaluation(evaluation_id=evaluation.evaluation_id)
        except Exception as cleanup_error:
            raise ExceptionGroup(
                "Foundry evaluation failed and cleanup also failed.",
                [original_error, cleanup_error],
            ) from original_error
        raise


def write_foundry_evaluation_certificate(
    certificate: FoundryNativeEvaluationCertificate,
    output_root: Path = DEFAULT_REPORT_ROOT,
    *,
    history_limit: int = DEFAULT_HISTORY_LIMIT,
) -> FoundryCertificatePaths:
    """Write bounded JSON and escaped HTML without prompts, responses, or judge reasons."""

    if history_limit <= 0:
        raise ValueError("History limit must be positive.")
    output_root.mkdir(parents=True, exist_ok=True)
    certificate_payload = certificate.model_dump(
        mode="json",
        by_alias=True,
        exclude={"report_url"},
    )
    report_value = certificate.report_url
    certificate_payload["reportUrl"] = _canonical_report_url(
        str(report_value) if report_value is not None else None
    )
    payload = json.dumps(
        certificate_payload,
        indent=2,
        sort_keys=True,
        ensure_ascii=True,
    )
    rendered = _render_certificate_html(certificate)
    stamp = certificate.generated_at.astimezone(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    json_path, html_path, reservation_path = _reserve_certificate_run_paths(output_root, stamp)
    latest_json = output_root / "latest.json"
    latest_html = output_root / "latest.html"
    try:
        for path, content in (
            (json_path, payload + "\n"),
            (html_path, rendered),
            (latest_json, payload + "\n"),
            (latest_html, rendered),
        ):
            _atomic_replace(path, content)
        _prune_history(output_root, history_limit)
    except Exception:
        json_path.unlink(missing_ok=True)
        html_path.unlink(missing_ok=True)
        raise
    finally:
        reservation_path.unlink(missing_ok=True)
    return FoundryCertificatePaths(
        json_path=json_path,
        html_path=html_path,
        latest_json_path=latest_json,
        latest_html_path=latest_html,
    )


def _new_foundry_run_name(suite_id: str) -> str:
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return f"{suite_id}-{stamp}-{uuid.uuid4().hex}"


def _reserve_certificate_run_paths(root: Path, stamp: str) -> tuple[Path, Path, Path]:
    for _ in range(100):
        token = uuid.uuid4().hex
        json_path = root / f"{_CERTIFICATE_RUN_PREFIX}{stamp}-{token}.json"
        html_path = root / f"{_CERTIFICATE_RUN_PREFIX}{stamp}-{token}.html"
        reservation_path = root / f".{_CERTIFICATE_RUN_PREFIX}{stamp}-{token}.lock"
        try:
            reservation_fd = os.open(
                reservation_path,
                os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                0o600,
            )
        except FileExistsError:
            continue
        os.close(reservation_fd)
        if json_path.exists() or html_path.exists():
            reservation_path.unlink(missing_ok=True)
            continue
        return json_path, html_path, reservation_path
    raise FileExistsError("Could not allocate a unique Agent IQ certificate run name.")


def _atomic_replace(path: Path, content: str) -> None:
    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{path.name}.",
            suffix=".tmp",
            dir=path.parent,
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _direct_target_data_source(suite: FoundryEvaluationSuite) -> object:
    from azure.ai.projects.models import AzureAIAgentTargetParam, TargetCompletionEvalRunDataSource
    from openai.types.evals.create_eval_completions_run_data_source_param import (
        SourceFileContent,
        SourceFileContentContent,
    )

    input_messages: Any = {
        "type": "template",
        "template": [
            {
                "type": "message",
                "role": "user",
                "content": {"type": "input_text", "text": "{{item.query}}"},
            }
        ],
    }
    return TargetCompletionEvalRunDataSource(
        type="azure_ai_target_completions",
        source=SourceFileContent(
            type="file_content",
            content=[SourceFileContentContent(item=_case_item(case)) for case in suite.cases],
        ),
        input_messages=input_messages,
        target=AzureAIAgentTargetParam(
            type="azure_ai_agent",
            name=suite.target_agent_name,
            version=suite.target_agent_version,
        ),
    )


def _case_item(case: object) -> dict[str, object]:
    mapped = _as_mapping(case)
    tool_definitions = mapped.get("toolDefinitions", ())
    normalized_tools: list[dict[str, object]] = []
    if isinstance(tool_definitions, Sequence) and not isinstance(tool_definitions, str):
        for tool in tool_definitions:
            item = _as_mapping(tool)
            normalized_tools.append(
                {
                    "type": "function",
                    "name": item["name"],
                    "description": item["description"],
                    "parameters": item["parameters"],
                }
            )
    return {
        "case_id": mapped["caseId"],
        "query": mapped["query"],
        "context": mapped.get("context"),
        "ground_truth": mapped.get("groundTruth"),
        "tool_definitions": normalized_tools,
    }


def _item_schema(suite: FoundryEvaluationSuite) -> dict[str, object]:
    referenced = {
        match.group(1)
        for criterion in suite.criteria
        for value in criterion.data_mapping.values()
        if (match := _ITEM_FIELD_RE.match(value))
    }
    properties: dict[str, object] = {
        "case_id": {"type": "string"},
        "query": {"type": "string"},
        "context": {"type": ["string", "null"]},
        "ground_truth": {"type": ["string", "null"]},
        "tool_definitions": {"type": "array", "items": {"type": "object"}},
    }
    unknown = referenced - properties.keys()
    if unknown:
        raise FoundryEvaluationCapabilityError(
            f"Unknown inline data mapping fields: {', '.join(sorted(unknown))}."
        )
    return {
        "type": "object",
        "properties": properties,
        "required": sorted(referenced | {"query"}),
    }


def _criterion_scores(
    definitions: tuple[FoundryCriterionDefinition, ...],
    output_items: tuple[Mapping[str, object], ...],
) -> tuple[FoundryCriterionScore, ...]:
    scores: list[FoundryCriterionScore] = []
    for definition in definitions:
        raw_scores: list[float] = []
        normalized_scores: list[float] = []
        failed = 0
        errored = 0
        skipped = 0
        for output in output_items:
            result = _find_criterion_result(output, definition.criterion_id)
            if result is None:
                errored += 1
                normalized_scores.append(0)
                continue
            if result.get("status") == "skipped":
                skipped += 1
                normalized_scores.append(0)
                continue
            passed = result.get("passed")
            if not isinstance(passed, bool):
                errored += 1
                normalized_scores.append(0)
                continue
            if not passed:
                failed += 1
            raw = result.get("score")
            if isinstance(raw, int | float) and not isinstance(raw, bool):
                raw_value = float(raw)
            else:
                raw_value = 1.0 if passed else 0.0
            if raw_value < 0:
                raise FoundryEvaluationResponseError(
                    f"Criterion {definition.criterion_id} returned a negative score."
                )
            raw_scores.append(raw_value)
            quality = raw_value / definition.normalization_divisor
            if definition.lower_is_better:
                quality = 1 - quality
            normalized_scores.append(min(1.0, max(0.0, quality)))
        native_mean = sum(raw_scores) / len(raw_scores) if raw_scores else None
        normalized_mean = sum(normalized_scores) / len(output_items)
        scores.append(
            FoundryCriterionScore(
                criterion_id=definition.criterion_id,
                evaluator_name=definition.evaluator_name,
                native_mean_score=native_mean,
                normalized_score=normalized_mean,
                samples=len(output_items),
                failed_samples=failed,
                errored_samples=errored,
                skipped_samples=skipped,
                passes=failed == 0 and errored == 0 and skipped == 0,
            )
        )
    return tuple(scores)


def _find_criterion_result(
    output: Mapping[str, object],
    criterion_id: str,
) -> Mapping[str, object] | None:
    results = output.get("results")
    if not isinstance(results, Sequence) or isinstance(results, str):
        return None
    matches = [
        _as_mapping(result) for result in results if _as_mapping(result).get("name") == criterion_id
    ]
    if len(matches) > 1:
        raise FoundryEvaluationResponseError(
            f"Criterion {criterion_id} appears more than once in an output item."
        )
    return matches[0] if matches else None


def _criterion_comparisons(
    definitions: tuple[FoundryCriterionDefinition, ...],
    scores: tuple[FoundryCriterionScore, ...],
    deterministic_report: RubricEvaluationReport,
    explanations: Mapping[str, str],
) -> tuple[FoundryCriterionComparison, ...]:
    deterministic_scores: dict[str, list[float]] = {}
    for result in deterministic_report.results:
        for criterion in result.criteria:
            deterministic_scores.setdefault(criterion.criterion_id, []).append(criterion.score)
    score_by_id = {score.criterion_id: score for score in scores}
    comparisons: list[FoundryCriterionComparison] = []
    for definition in definitions:
        if definition.agent_iq_criterion_id is None:
            continue
        values = deterministic_scores.get(definition.agent_iq_criterion_id)
        if not values:
            raise FoundryEvaluationResponseError(
                "The deterministic report is missing comparison criterion "
                f"{definition.agent_iq_criterion_id}."
            )
        foundry_score = score_by_id[definition.criterion_id].normalized_score
        agent_iq_score = sum(values) / len(values)
        delta = abs(foundry_score - agent_iq_score)
        explanation = explanations.get(definition.criterion_id)
        comparisons.append(
            FoundryCriterionComparison(
                foundry_criterion_id=definition.criterion_id,
                agent_iq_criterion_id=definition.agent_iq_criterion_id,
                foundry_score=foundry_score,
                agent_iq_score=agent_iq_score,
                absolute_delta=delta,
                maximum_unexplained_delta=definition.max_score_delta,
                public_explanation_code=explanation,
                passes=delta <= definition.max_score_delta or explanation is not None,
            )
        )
    return tuple(comparisons)


def _model_usage(
    records: tuple[Mapping[str, object], ...],
    prices: tuple[FoundryModelPrice, ...],
) -> tuple[FoundryModelUsage, ...]:
    concrete = [
        record
        for record in records
        if _usage_name(record).casefold() not in _AGGREGATE_USAGE_ALIASES
    ]
    selected = concrete or list(records)
    aggregated: dict[str, list[int]] = {}
    for record in selected:
        name = _usage_name(record)
        prompt = _integer(record, "prompt_tokens")
        cached = _integer(record, "cached_tokens")
        completion = _integer(record, "completion_tokens")
        total = _integer(record, "total_tokens")
        invocations = _integer(record, "invocation_count")
        if total != prompt + completion:
            raise FoundryEvaluationResponseError(f"Model usage total is inconsistent for {name}.")
        values = aggregated.setdefault(name, [0, 0, 0, 0, 0])
        for index, value in enumerate((invocations, prompt, cached, completion, total)):
            values[index] += value

    usage: list[FoundryModelUsage] = []
    for name, values in sorted(aggregated.items()):
        invocations, prompt, cached, completion, total = values
        cost = _estimate_cost(name, prompt, cached, completion, prices)
        if cost is not None:
            pricing_status = FoundryModelPricingStatus.PRICED
        elif name.casefold() in _SERVICE_MANAGED_USAGE_ALIASES:
            pricing_status = FoundryModelPricingStatus.SERVICE_MANAGED_UNPRICED
        else:
            pricing_status = FoundryModelPricingStatus.UNKNOWN
        usage.append(
            FoundryModelUsage(
                model=name,
                invocations=invocations,
                input_tokens=prompt,
                cached_input_tokens=cached,
                output_tokens=completion,
                total_tokens=total,
                estimated_cost_usd=cost,
                pricing_status=pricing_status,
            )
        )
    return tuple(usage)


def _estimate_cost(
    model: str,
    input_tokens: int,
    cached_tokens: int,
    output_tokens: int,
    prices: tuple[FoundryModelPrice, ...],
) -> float | None:
    matching = [
        price for price in prices if model.casefold().startswith(price.model_prefix.casefold())
    ]
    if not matching:
        return None
    price = max(matching, key=lambda item: len(item.model_prefix))
    uncached_tokens = input_tokens - cached_tokens
    estimate = (
        uncached_tokens * price.input_usd_per_million
        + cached_tokens * price.cached_input_usd_per_million
        + output_tokens * price.output_usd_per_million
    ) / 1_000_000
    return round(estimate, 12)


def _result_counts(
    service_counts: Mapping[str, int],
    output_items: tuple[Mapping[str, object], ...],
) -> FoundryEvaluationResultCounts:
    total = _integer(service_counts, "total")
    passed = _integer(service_counts, "passed")
    failed = _integer(service_counts, "failed")
    errors = _integer(service_counts, "errored", fallback_key="errors")
    if total != len(output_items):
        raise FoundryEvaluationResponseError(
            "Foundry result count does not match returned output items."
        )
    return FoundryEvaluationResultCounts(
        total=total,
        passed=passed,
        failed=failed,
        errors=errors,
    )


def _run_snapshot(run: object) -> EvaluationRunSnapshot:
    mapped = _as_mapping(run)
    result_counts = _as_mapping(mapped.get("result_counts", {}))
    usage = mapped.get("per_model_usage", ())
    usage_records = (
        tuple(_as_mapping(item) for item in usage)
        if isinstance(usage, Sequence) and not isinstance(usage, str)
        else ()
    )
    report_value = mapped.get("report_url", mapped.get("reportUrl"))
    report_url = _canonical_report_url(_optional_string(report_value))
    return EvaluationRunSnapshot(
        run_id=str(mapped["id"]),
        status=str(mapped["status"]),
        report_url=report_url,
        result_counts={
            str(key): int(value) for key, value in result_counts.items() if isinstance(value, int)
        },
        per_model_usage=usage_records,
        started_at=_optional_datetime(mapped.get("started_at")),
        completed_at=_optional_datetime(mapped.get("completed_at")),
    )


def _run_status(value: str) -> FoundryEvaluationRunStatus:
    try:
        return FoundryEvaluationRunStatus(value)
    except ValueError as exc:
        raise FoundryEvaluationResponseError(
            f"Foundry returned unknown run status {value!r}."
        ) from exc


def _run_duration_ms(run: EvaluationRunSnapshot, *, fallback_seconds: float) -> int:
    if run.started_at is not None and run.completed_at is not None:
        seconds = (run.completed_at - run.started_at).total_seconds()
        if seconds < 0:
            raise FoundryEvaluationResponseError("Foundry run completion precedes its start.")
        return round(seconds * 1000)
    return round(fallback_seconds * 1000)


def _suite_hash(suite: FoundryEvaluationSuite) -> str:
    payload = json.dumps(
        suite.model_dump(mode="json", by_alias=True),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _render_certificate_html(certificate: FoundryNativeEvaluationCertificate) -> str:
    status = "PASS" if certificate.passes else "FAIL"
    criterion_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.criterion_id)}</td>"
        f"<td>{html.escape(item.evaluator_name)}</td>"
        f"<td>{item.normalized_score:.3f}</td>"
        f"<td>{item.failed_samples}</td>"
        f"<td>{item.errored_samples}</td>"
        f"<td>{item.skipped_samples}</td>"
        f"<td>{'PASS' if item.passes else 'FAIL'}</td>"
        "</tr>"
        for item in certificate.criteria
    )
    comparison_rows = "".join(
        "<tr>"
        f"<td>{html.escape(item.foundry_criterion_id)}</td>"
        f"<td>{html.escape(item.agent_iq_criterion_id)}</td>"
        f"<td>{item.foundry_score:.3f}</td>"
        f"<td>{item.agent_iq_score:.3f}</td>"
        f"<td>{item.absolute_delta:.3f}</td>"
        f"<td>{html.escape(item.public_explanation_code or '')}</td>"
        f"<td>{'PASS' if item.passes else 'FAIL'}</td>"
        "</tr>"
        for item in certificate.comparisons
    )
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        "<title>Agent IQ Foundry evaluation</title>"
        "<style>body{font:14px system-ui;margin:2rem;color:#17202a}"
        "table{border-collapse:collapse;width:100%;margin:1rem 0}"
        "th,td{border:1px solid #ccd3db;padding:.55rem;text-align:left}"
        "th{background:#eef2f6}.pass{color:#116329}.fail{color:#b42318}</style>"
        "</head><body>"
        f'<h1>Native Foundry evaluation: <span class="{status.lower()}">{status}</span></h1>'
        f"<p>Suite {html.escape(certificate.suite_id)} "
        f"{html.escape(certificate.suite_version)}; target "
        f"{html.escape(certificate.target_agent_name)} version "
        f"{html.escape(certificate.target_agent_version)}.</p>"
        f"<p>Mode {html.escape(certificate.target_mode.value)}; "
        f"duration {certificate.duration_ms} ms; tokens {certificate.total_tokens}; "
        f"estimated priced-model cost "
        f"{certificate.estimated_priced_model_cost_usd!s} USD.</p>"
        "<h2>Native criteria</h2><table><thead><tr>"
        "<th>Criterion</th><th>Evaluator</th><th>Normalized</th>"
        "<th>Failed</th><th>Errors</th><th>Skipped</th><th>Gate</th>"
        f"</tr></thead><tbody>{criterion_rows}</tbody></table>"
        "<h2>Deterministic comparison</h2><table><thead><tr>"
        "<th>Foundry</th><th>Agent IQ</th><th>Foundry score</th>"
        "<th>Agent IQ score</th><th>Delta</th><th>Explanation</th><th>Gate</th>"
        f"</tr></thead><tbody>{comparison_rows}</tbody></table>"
        "</body></html>\n"
    )


def _prune_history(root: Path, limit: int) -> None:
    if limit <= 0:
        raise ValueError("History limit must be positive.")
    for suffix in (".json", ".html"):
        history = sorted(
            path
            for path in root.glob(f"*{suffix}")
            if path.is_file() and _CERTIFICATE_HISTORY_NAME_RE.fullmatch(path.name)
        )
        for path in history[:-limit]:
            path.unlink()


def _find_azd() -> str:
    resolved = shutil.which("azd")
    if resolved is not None:
        return resolved
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        candidate = Path(local_app_data) / "Programs" / "Azure Dev CLI" / "azd.exe"
        if candidate.is_file():
            return str(candidate)
    raise FoundryEvaluationCapabilityError("azd executable was not found.")


def _run_command(arguments: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(arguments),
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
        timeout=30,
    )


def _numeric_version(value: str) -> tuple[int, int, int]:
    numbers = [int(item) for item in re.findall(r"\d+", value)[:3]]
    padded = [*numbers, 0, 0, 0]
    return padded[0], padded[1], padded[2]


def _usage_name(record: Mapping[str, object]) -> str:
    value = record.get("model_name") or record.get("run_model_name")
    if not isinstance(value, str) or not value:
        raise FoundryEvaluationResponseError("Foundry model usage has no model name.")
    return value


def _integer(
    mapping: Mapping[str, object],
    key: str,
    *,
    fallback_key: str | None = None,
) -> int:
    value = mapping.get(key)
    if value is None and fallback_key is not None:
        value = mapping.get(fallback_key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise FoundryEvaluationResponseError(
            f"Foundry response field {key!r} is not a non-negative integer."
        )
    return value


def _as_mapping(value: object) -> Mapping[str, object]:
    if isinstance(value, Mapping):
        return value
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        dumped = model_dump(mode="python", by_alias=True)
        if isinstance(dumped, Mapping):
            return dumped
    raise FoundryEvaluationResponseError(
        f"Expected a mapping-compatible Foundry response, got {type(value).__name__}."
    )


def _optional_string(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise FoundryEvaluationResponseError("Expected an optional string.")
    return value


def _optional_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise FoundryEvaluationResponseError("Foundry timestamp must be timezone-aware.")
        return parsed
    raise FoundryEvaluationResponseError("Expected an optional Foundry timestamp.")


def _sdk_operation_error(operation: str, exc: Exception) -> FoundryEvaluationRunError:
    status_code = getattr(exc, "status_code", None)
    suffix = f" (HTTP {status_code})" if isinstance(status_code, int) else ""
    return FoundryEvaluationRunError(f"Foundry could not {operation}{suffix}.")
