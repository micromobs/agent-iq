"""Versioned rubric, replay, and scorecard contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any
from unicodedata import category
from urllib.parse import urlsplit, urlunsplit

from pydantic import (
    AnyHttpUrl,
    Field,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

from .base import (
    ContractModel,
    Identifier,
    NonEmptyText,
    SchemaVersion,
    Sha256Digest,
    ShortText,
    Timestamp,
    VersionText,
    require_unique_text,
)
from .execution import ExecutionStatus
from .synthesis import SynthesisStatus

Percent = Annotated[float, Field(ge=0, le=1)]
NonNegativeFloat = Annotated[float, Field(ge=0)]


def _canonical_report_url(value: object) -> str | None:
    """Return only a credential-free, query-free HTTPS report URL."""

    if value is None:
        return None
    if not isinstance(value, str):
        try:
            value = str(value)
        except Exception:
            return None
    if (
        not value
        or value != value.strip()
        or any(char.isspace() or category(char) == "Cc" for char in value)
        or "\\" in value
        or "?" in value
        or "#" in value
    ):
        return None
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme.casefold() != "https"
        or not parsed.netloc
        or not hostname
        or parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
        or "@" in parsed.netloc
        or "%" in parsed.netloc
        or parsed.netloc.endswith(":")
    ):
        return None
    try:
        canonical_host = hostname.encode("idna").decode("ascii").casefold()
    except UnicodeError:
        return None
    if ":" in canonical_host:
        canonical_host = f"[{canonical_host}]"
    canonical_netloc = canonical_host
    if port is not None and port != 443:
        canonical_netloc += f":{port}"
    path = parsed.path or "/"
    if (
        not path.startswith("/")
        or "%" in path
        or "//" in path
        or any(segment in {".", ".."} for segment in path.split("/"))
    ):
        return None
    if path != "/":
        path = path.rstrip("/") or "/"
    return urlunsplit(("https", canonical_netloc, path, "", ""))


class RubricDimension(StrEnum):
    ROUTING = "routing"
    ORCHESTRATION = "orchestration"
    GROUNDEDNESS = "groundedness"
    EVIDENCE_COVERAGE = "evidence_coverage"
    HONEST_DECLINE = "honest_decline"
    CONFLICT_HANDLING = "conflict_handling"
    COMPLETENESS = "completeness"
    TASK_SUCCESS = "task_success"


class RubricCriterionMode(StrEnum):
    DETERMINISTIC = "deterministic"
    MODEL_JUDGE = "model_judge"


class RubricEvaluationMode(StrEnum):
    DETERMINISTIC = "deterministic"
    HYBRID = "hybrid"


class RubricObservationSource(StrEnum):
    COWORK_TRANSCRIPT = "cowork_transcript"
    EXECUTION_TRACE = "execution_trace"


class RubricCriterion(ContractModel):
    criterion_id: Identifier
    dimension: RubricDimension
    mode: RubricCriterionMode
    evaluator: Identifier
    description: ShortText
    weight: float = Field(gt=0, le=100)
    threshold: float = Field(ge=0, le=1)
    required: bool = True


class RubricDefinition(ContractModel):
    schema_version: SchemaVersion = "1.0"
    rubric_id: Identifier
    version: VersionText
    title: ShortText
    description: ShortText
    pass_threshold: float = Field(gt=0, le=1)
    criteria: tuple[RubricCriterion, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_criteria(self) -> RubricDefinition:
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Rubric criterion IDs must be unique.")
        if not any(
            criterion.mode is RubricCriterionMode.DETERMINISTIC for criterion in self.criteria
        ):
            raise ValueError("A rubric requires at least one deterministic criterion.")
        return self


class RubricPlanStep(ContractModel):
    agent_id: Identifier
    depends_on_agent_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_dependencies(self) -> RubricPlanStep:
        if len(self.depends_on_agent_ids) != len(set(self.depends_on_agent_ids)):
            raise ValueError("Rubric plan dependencies must be unique.")
        if self.agent_id in self.depends_on_agent_ids:
            raise ValueError("A rubric plan step cannot depend on itself.")
        return self


class RubricCase(ContractModel):
    case_id: Identifier
    rubric_id: Identifier
    rubric_version: VersionText
    expected_agent_ids: tuple[Identifier, ...]
    expected_plan: tuple[RubricPlanStep, ...]
    expected_synthesis_status: SynthesisStatus
    expected_execution_status: ExecutionStatus
    expected_evidence_ids: tuple[Identifier, ...] = ()
    required_answer_terms: tuple[ShortText, ...] = ()
    forbidden_answer_terms: tuple[ShortText, ...] = ()
    expected_conflict: bool = False
    expected_visible_failure_ids: tuple[Identifier, ...] = ()
    required_output_ids: tuple[Identifier, ...] = ()

    @field_validator(
        "expected_agent_ids",
        "expected_evidence_ids",
        "expected_visible_failure_ids",
        "required_output_ids",
    )
    @classmethod
    def validate_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Rubric case identifiers must be unique.")
        return values

    @field_validator("required_answer_terms", "forbidden_answer_terms")
    @classmethod
    def validate_unique_terms(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return require_unique_text(values, "Rubric answer terms")

    @model_validator(mode="after")
    def validate_expectations(self) -> RubricCase:
        plan_agents = [step.agent_id for step in self.expected_plan]
        if len(plan_agents) != len(set(plan_agents)):
            raise ValueError("Expected rubric plan agents must be unique.")
        if set(plan_agents) != set(self.expected_agent_ids):
            raise ValueError("Expected rubric plan agents must match expected selection.")
        _validate_plan_graph(self.expected_plan)
        if set(self.expected_visible_failure_ids) - set(self.expected_agent_ids):
            raise ValueError("Expected failures must reference selected agents.")
        required = {term.casefold() for term in self.required_answer_terms}
        forbidden = {term.casefold() for term in self.forbidden_answer_terms}
        if required & forbidden:
            raise ValueError("Required and forbidden answer terms must not overlap.")
        if self.expected_synthesis_status is SynthesisStatus.DECLINED and (
            self.expected_evidence_ids or self.expected_conflict
        ):
            raise ValueError("An expected decline cannot require evidence or conflicts.")
        if self.expected_conflict and (
            self.expected_synthesis_status is not SynthesisStatus.PARTIAL
            or self.expected_execution_status is not ExecutionStatus.PARTIAL
        ):
            raise ValueError("An expected conflict requires a partial outcome.")
        return self


class RubricObservation(ContractModel):
    observation_id: Identifier
    case_id: Identifier
    rubric_id: Identifier
    rubric_version: VersionText
    source: RubricObservationSource
    selected_agent_ids: tuple[Identifier, ...]
    actual_plan: tuple[RubricPlanStep, ...]
    synthesis_status: SynthesisStatus
    execution_status: ExecutionStatus
    answer: NonEmptyText
    evidence_ids: tuple[Identifier, ...] = ()
    cited_evidence_ids: tuple[Identifier, ...] = ()
    total_claims: int = Field(ge=0)
    grounded_claims: int = Field(ge=0)
    conflict_claim_ids: tuple[Identifier, ...] = ()
    visible_failure_ids: tuple[Identifier, ...] = ()
    delivered_output_ids: tuple[Identifier, ...] = ()

    @field_validator(
        "selected_agent_ids",
        "evidence_ids",
        "cited_evidence_ids",
        "conflict_claim_ids",
        "visible_failure_ids",
        "delivered_output_ids",
    )
    @classmethod
    def validate_unique_ids(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Rubric observation identifiers must be unique.")
        return values

    @model_validator(mode="after")
    def validate_observation(self) -> RubricObservation:
        plan_agents = [step.agent_id for step in self.actual_plan]
        if len(plan_agents) != len(set(plan_agents)):
            raise ValueError("Observed rubric plan agents must be unique.")
        _validate_plan_graph(self.actual_plan)
        if self.grounded_claims > self.total_claims:
            raise ValueError("Grounded claims cannot exceed total observed claims.")
        if set(self.cited_evidence_ids) - set(self.evidence_ids):
            raise ValueError("Observed citations must reference observed evidence.")
        if set(self.visible_failure_ids) - set(self.selected_agent_ids):
            raise ValueError("Visible failures must reference selected agents.")
        return self


class ModelJudgment(ContractModel):
    observation_id: Identifier
    rubric_id: Identifier
    rubric_version: VersionText
    criterion_id: Identifier
    provider: ShortText
    model: ShortText
    prompt_version: VersionText
    score: float = Field(ge=0, le=1)
    public_reason: ShortText


class RubricReplayBundle(ContractModel):
    schema_version: SchemaVersion = "1.0"
    suite_id: Identifier
    rubric: RubricDefinition
    cases: tuple[RubricCase, ...] = Field(min_length=1)
    observations: tuple[RubricObservation, ...] = Field(min_length=1)
    judgments: tuple[ModelJudgment, ...] = ()

    @model_validator(mode="after")
    def validate_bundle(self) -> RubricReplayBundle:
        case_ids = [case.case_id for case in self.cases]
        if len(case_ids) != len(set(case_ids)):
            raise ValueError("Rubric replay case IDs must be unique.")
        observation_ids = [observation.observation_id for observation in self.observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("Rubric replay observation IDs must be unique.")

        expected_version = (self.rubric.rubric_id, self.rubric.version)
        versioned_items: tuple[RubricCase | RubricObservation | ModelJudgment, ...] = (
            *self.cases,
            *self.observations,
            *self.judgments,
        )
        for item in versioned_items:
            if (item.rubric_id, item.rubric_version) != expected_version:
                raise ValueError("Rubric replay input version does not match its definition.")

        available_cases = set(case_ids)
        observed_cases = {observation.case_id for observation in self.observations}
        if observed_cases - available_cases:
            raise ValueError("Every rubric observation must reference a known case.")
        if available_cases - observed_cases:
            raise ValueError("Every rubric case requires at least one observation.")

        model_criteria = {
            criterion.criterion_id
            for criterion in self.rubric.criteria
            if criterion.mode is RubricCriterionMode.MODEL_JUDGE
        }
        available_observations = set(observation_ids)
        judgment_keys: set[tuple[str, str]] = set()
        for judgment in self.judgments:
            key = (judgment.observation_id, judgment.criterion_id)
            if key in judgment_keys:
                raise ValueError("Model judgments must be unique per observation and criterion.")
            judgment_keys.add(key)
            if judgment.observation_id not in available_observations:
                raise ValueError("A model judgment must reference a known observation.")
            if judgment.criterion_id not in model_criteria:
                raise ValueError("A model judgment must reference a model-judge criterion.")
        return self


class CriterionScore(ContractModel):
    criterion_id: Identifier
    dimension: RubricDimension
    mode: RubricCriterionMode
    score: float = Field(ge=0, le=1)
    threshold: float = Field(ge=0, le=1)
    weight: float = Field(gt=0, le=100)
    weighted_score: float = Field(ge=0, le=100)
    required: bool
    passes: bool
    public_reason: ShortText
    provenance: tuple[ShortText, ...] = ()

    @model_validator(mode="after")
    def validate_score(self) -> CriterionScore:
        if abs(self.weighted_score - (self.score * self.weight)) > 1e-9:
            raise ValueError("Criterion weighted score does not match score and weight.")
        if self.passes != (self.score >= self.threshold):
            raise ValueError("Criterion pass state does not match its threshold.")
        return self


class RubricCaseScore(ContractModel):
    case_id: Identifier
    observation_id: Identifier
    source: RubricObservationSource
    score: float = Field(ge=0, le=1)
    pass_threshold: float = Field(gt=0, le=1)
    passes: bool
    criteria: tuple[CriterionScore, ...] = Field(min_length=1)
    skipped_criterion_ids: tuple[Identifier, ...] = ()

    @model_validator(mode="after")
    def validate_case_score(self) -> RubricCaseScore:
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("Evaluated criterion IDs must be unique.")
        if len(self.skipped_criterion_ids) != len(set(self.skipped_criterion_ids)):
            raise ValueError("Skipped criterion IDs must be unique.")
        if set(criterion_ids) & set(self.skipped_criterion_ids):
            raise ValueError("A criterion cannot be both evaluated and skipped.")
        total_weight = sum(criterion.weight for criterion in self.criteria)
        expected_score = sum(criterion.weighted_score for criterion in self.criteria) / total_weight
        if abs(self.score - expected_score) > 1e-9:
            raise ValueError("Case score does not match its weighted criteria.")
        expected_pass = self.score >= self.pass_threshold and all(
            criterion.passes for criterion in self.criteria if criterion.required
        )
        if self.passes != expected_pass:
            raise ValueError("Case pass state does not match its criteria.")
        return self


class RubricEvaluationReport(ContractModel):
    schema_version: SchemaVersion = "1.0"
    suite_id: Identifier
    rubric_id: Identifier
    rubric_version: VersionText
    rubric_sha256: Sha256Digest
    evaluation_mode: RubricEvaluationMode
    generated_at: Timestamp
    observations: int = Field(gt=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    mean_score: float = Field(ge=0, le=1)
    model_judgments: int = Field(ge=0)
    passes: bool
    results: tuple[RubricCaseScore, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_report(self) -> RubricEvaluationReport:
        if self.observations != len(self.results):
            raise ValueError("Report observation count must match result count.")
        if self.passed + self.failed != self.observations:
            raise ValueError("Report pass and failure counts must cover every observation.")
        expected_passed = sum(1 for result in self.results if result.passes)
        if self.passed != expected_passed:
            raise ValueError("Report pass count does not match case results.")
        expected_mean = sum(result.score for result in self.results) / len(self.results)
        if abs(self.mean_score - expected_mean) > 1e-9:
            raise ValueError("Report mean score does not match case results.")
        if self.passes != (self.failed == 0):
            raise ValueError("Report pass state does not match its failures.")
        result_ids = [result.observation_id for result in self.results]
        if len(result_ids) != len(set(result_ids)):
            raise ValueError("Report observation IDs must be unique.")
        return self


def _validate_plan_graph(steps: tuple[RubricPlanStep, ...]) -> None:
    available = {step.agent_id for step in steps}
    dependencies = {step.agent_id: set(step.depends_on_agent_ids) for step in steps}
    if any(values - available for values in dependencies.values()):
        raise ValueError("Rubric plan dependencies must reference plan agents.")

    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(agent_id: str) -> None:
        if agent_id in visiting:
            raise ValueError("Rubric plan dependencies must be acyclic.")
        if agent_id in visited:
            return
        visiting.add(agent_id)
        for dependency in dependencies[agent_id]:
            visit(dependency)
        visiting.remove(agent_id)
        visited.add(agent_id)

    for agent_id in dependencies:
        visit(agent_id)


class FoundryEvaluationTargetMode(StrEnum):
    """Supported native Foundry evaluation data sources."""

    DIRECT_TARGET = "direct_target"
    TRACE_REPLAY = "trace_replay"


class FoundryEvaluationProtocol(StrEnum):
    """Agent protocols relevant to Foundry evaluation capability selection."""

    RESPONSES = "responses"
    INVOCATIONS = "invocations"
    A2A = "a2a"
    ACTIVITY = "activity"


class FoundryInteractionPattern(StrEnum):
    """Execution patterns that determine direct-target eligibility."""

    SYNCHRONOUS_NON_STREAMING = "synchronous_non_streaming"
    STREAMING = "streaming"
    DURABLE = "durable"


class FoundryCriterionCategory(StrEnum):
    """Native evaluator families represented in a Foundry suite."""

    AGENT = "agent"
    RAG = "rag"
    SAFETY = "safety"


class FoundryJudgeParameter(StrEnum):
    """Initialization parameter expected by the selected built-in evaluator."""

    NONE = "none"
    MODEL = "model"
    DEPLOYMENT_NAME = "deployment_name"


class FoundryEvaluationRunStatus(StrEnum):
    """Terminal and nonterminal statuses returned by Foundry evaluations."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class FoundryModelPricingStatus(StrEnum):
    """Cost treatment for one model-usage row."""

    PRICED = "priced"
    SERVICE_MANAGED_UNPRICED = "service_managed_unpriced"
    UNKNOWN = "unknown"


class FoundryEvaluationToolDefinition(ContractModel):
    """OpenAI-compatible tool definition made available to native evaluators."""

    name: Identifier
    description: ShortText
    parameters: dict[str, Any]


class FoundryEvaluationCase(ContractModel):
    """Versioned content used only as native evaluation input."""

    case_id: Identifier
    query: NonEmptyText
    context: NonEmptyText | None = None
    ground_truth: NonEmptyText | None = None
    tool_definitions: tuple[FoundryEvaluationToolDefinition, ...] = ()

    @model_validator(mode="after")
    def validate_case(self) -> FoundryEvaluationCase:
        require_unique_text(
            tuple(tool.name for tool in self.tool_definitions),
            "Foundry evaluation tool names",
        )
        return self


class FoundryCriterionDefinition(ContractModel):
    """Versioned mapping and normalization for one Foundry built-in evaluator."""

    criterion_id: Identifier
    evaluator_name: Identifier
    category: FoundryCriterionCategory
    judge_parameter: FoundryJudgeParameter
    data_mapping: dict[str, ShortText]
    normalization_divisor: Annotated[float, Field(gt=0)]
    lower_is_better: bool = False
    agent_iq_criterion_id: Identifier | None = None
    max_score_delta: Percent = 0.25


class FoundryModelPrice(ContractModel):
    """Public list pricing used for a bounded evaluation-cost estimate."""

    model_prefix: ShortText
    input_usd_per_million: NonNegativeFloat
    cached_input_usd_per_million: NonNegativeFloat
    output_usd_per_million: NonNegativeFloat


class FoundryEvaluationSuite(ContractModel):
    """Versioned native Foundry evaluator suite and release budgets."""

    schema_version: SchemaVersion = "1.0"
    suite_id: Identifier
    suite_version: VersionText
    target_mode: FoundryEvaluationTargetMode
    protocol: FoundryEvaluationProtocol
    interaction_pattern: FoundryInteractionPattern
    target_agent_name: Identifier
    target_agent_version: VersionText
    judge_model: Identifier
    criteria: tuple[FoundryCriterionDefinition, ...]
    cases: tuple[FoundryEvaluationCase, ...] = ()
    max_samples: PositiveInt
    max_duration_ms: PositiveInt
    max_total_tokens: PositiveInt
    max_estimated_priced_model_cost_usd: NonNegativeFloat | None = None
    allow_service_managed_unpriced_usage: bool = False
    model_prices: tuple[FoundryModelPrice, ...] = ()

    @model_validator(mode="after")
    def validate_suite(self) -> FoundryEvaluationSuite:
        require_unique_text(
            tuple(criterion.criterion_id for criterion in self.criteria),
            "Foundry criterion ids",
        )
        require_unique_text(
            tuple(criterion.evaluator_name for criterion in self.criteria),
            "Foundry evaluator names",
        )
        require_unique_text(
            tuple(case.case_id for case in self.cases),
            "Foundry evaluation case ids",
        )
        require_unique_text(
            tuple(price.model_prefix for price in self.model_prices),
            "Foundry model price prefixes",
        )
        if not self.criteria:
            raise ValueError("A Foundry evaluation suite requires criteria.")
        direct_supported = (
            self.protocol
            in {
                FoundryEvaluationProtocol.RESPONSES,
                FoundryEvaluationProtocol.INVOCATIONS,
            }
            and self.interaction_pattern is FoundryInteractionPattern.SYNCHRONOUS_NON_STREAMING
        )
        if self.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET and not direct_supported:
            raise ValueError(
                "Direct target evaluation only supports synchronous non-streaming "
                "Responses or Invocations agents."
            )
        if self.target_mode is FoundryEvaluationTargetMode.DIRECT_TARGET and not self.cases:
            raise ValueError("Direct target evaluation requires inline cases.")
        if len(self.cases) > self.max_samples:
            raise ValueError("Foundry evaluation cases exceed the suite sample limit.")
        if self.max_estimated_priced_model_cost_usd is not None and not self.model_prices:
            raise ValueError("A cost budget requires at least one model price.")
        return self


class FoundryCriterionScore(ContractModel):
    """Content-safe aggregate for one native Foundry criterion."""

    criterion_id: Identifier
    evaluator_name: Identifier
    native_mean_score: NonNegativeFloat | None = None
    normalized_score: Percent
    samples: PositiveInt
    failed_samples: NonNegativeInt
    errored_samples: NonNegativeInt
    skipped_samples: NonNegativeInt
    passes: bool

    @model_validator(mode="after")
    def validate_counts(self) -> FoundryCriterionScore:
        if self.failed_samples + self.errored_samples + self.skipped_samples > self.samples:
            raise ValueError("Foundry criterion failures exceed its sample count.")
        expected_pass = (
            self.failed_samples == 0 and self.errored_samples == 0 and self.skipped_samples == 0
        )
        if self.passes != expected_pass:
            raise ValueError("Foundry criterion passes is inconsistent with its counts.")
        return self


class FoundryCriterionComparison(ContractModel):
    """Normalized comparison with the matching deterministic Agent IQ criterion."""

    foundry_criterion_id: Identifier
    agent_iq_criterion_id: Identifier
    foundry_score: Percent
    agent_iq_score: Percent
    absolute_delta: Percent
    maximum_unexplained_delta: Percent
    public_explanation_code: Identifier | None = None
    passes: bool

    @model_validator(mode="after")
    def validate_comparison(self) -> FoundryCriterionComparison:
        expected_delta = abs(self.foundry_score - self.agent_iq_score)
        if abs(self.absolute_delta - expected_delta) > 1e-9:
            raise ValueError("Foundry criterion comparison delta is inconsistent.")
        expected_pass = (
            self.absolute_delta <= self.maximum_unexplained_delta
            or self.public_explanation_code is not None
        )
        if self.passes != expected_pass:
            raise ValueError("Foundry criterion comparison pass state is inconsistent.")
        return self


class FoundryModelUsage(ContractModel):
    """De-duplicated model usage from a native Foundry evaluation run."""

    model: Identifier
    invocations: NonNegativeInt
    input_tokens: NonNegativeInt
    cached_input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt
    total_tokens: NonNegativeInt
    estimated_cost_usd: NonNegativeFloat | None = None
    pricing_status: FoundryModelPricingStatus

    @model_validator(mode="after")
    def validate_tokens(self) -> FoundryModelUsage:
        if self.cached_input_tokens > self.input_tokens:
            raise ValueError("Cached input tokens cannot exceed input tokens.")
        expected_total = self.input_tokens + self.output_tokens
        if self.total_tokens != expected_total:
            raise ValueError("Foundry model token total is inconsistent.")
        expected_priced = self.estimated_cost_usd is not None
        if expected_priced != (self.pricing_status is FoundryModelPricingStatus.PRICED):
            raise ValueError("Foundry model pricing status is inconsistent.")
        return self


class FoundryEvaluationResultCounts(ContractModel):
    """Content-safe native run result counts."""

    total: PositiveInt
    passed: NonNegativeInt
    failed: NonNegativeInt
    errors: NonNegativeInt

    @model_validator(mode="after")
    def validate_total(self) -> FoundryEvaluationResultCounts:
        if self.total != self.passed + self.failed + self.errors:
            raise ValueError("Foundry result counts do not sum to total.")
        return self


class FoundryEvaluationTooling(ContractModel):
    """Version provenance for SDK and azd evaluation tooling."""

    azure_ai_projects: VersionText
    openai: VersionText
    azd: VersionText
    microsoft_foundry_extension: VersionText
    azure_ai_agents_extension: VersionText


class FoundryNativeEvaluationCertificate(ContractModel):
    """Content-safe release evidence for a native Foundry evaluation run."""

    schema_version: SchemaVersion = "1.0"
    certificate_id: Identifier
    generated_at: Timestamp
    suite_id: Identifier
    suite_version: VersionText
    suite_sha256: Sha256Digest
    target_mode: FoundryEvaluationTargetMode
    protocol: FoundryEvaluationProtocol
    interaction_pattern: FoundryInteractionPattern
    target_agent_name: Identifier
    target_agent_version: VersionText
    judge_model: Identifier
    evaluation_id: Identifier
    run_id: Identifier
    run_status: FoundryEvaluationRunStatus
    report_url: AnyHttpUrl | None = None
    tooling: FoundryEvaluationTooling
    result_counts: FoundryEvaluationResultCounts
    criteria: tuple[FoundryCriterionScore, ...]
    comparisons: tuple[FoundryCriterionComparison, ...]
    model_usage: tuple[FoundryModelUsage, ...]
    duration_ms: NonNegativeInt
    max_samples: PositiveInt
    max_duration_ms: PositiveInt
    total_tokens: NonNegativeInt
    max_total_tokens: PositiveInt
    estimated_priced_model_cost_usd: NonNegativeFloat | None = None
    max_estimated_priced_model_cost_usd: NonNegativeFloat | None = None
    priced_model_cost_complete: bool
    allow_service_managed_unpriced_usage: bool
    passes: bool

    @field_validator("report_url", mode="before")
    @classmethod
    def sanitize_report_url(cls, value: object) -> str | None:
        return _canonical_report_url(value)

    @model_validator(mode="after")
    def validate_certificate(self) -> FoundryNativeEvaluationCertificate:
        require_unique_text(
            tuple(criterion.criterion_id for criterion in self.criteria),
            "Foundry certificate criterion ids",
        )
        require_unique_text(
            tuple(comparison.foundry_criterion_id for comparison in self.comparisons),
            "Foundry comparison criterion ids",
        )
        require_unique_text(
            tuple(usage.model for usage in self.model_usage),
            "Foundry usage models",
        )
        if not self.criteria:
            raise ValueError("A Foundry certificate requires criterion results.")
        if self.result_counts.total > self.max_samples:
            raise ValueError("Foundry result count exceeds the suite sample limit.")
        if self.result_counts.total != self.criteria[0].samples:
            raise ValueError("Foundry result counts must match criterion sample counts.")
        if any(criterion.samples != self.result_counts.total for criterion in self.criteria):
            raise ValueError("All Foundry criteria must cover every evaluation sample.")
        usage_tokens = sum(usage.total_tokens for usage in self.model_usage)
        if self.total_tokens != usage_tokens:
            raise ValueError("Foundry certificate token total is inconsistent.")
        known_costs = [
            usage.estimated_cost_usd
            for usage in self.model_usage
            if usage.estimated_cost_usd is not None
        ]
        expected_cost = sum(known_costs) if known_costs else None
        if self.estimated_priced_model_cost_usd != expected_cost:
            raise ValueError("Foundry certificate cost estimate is inconsistent.")
        expected_cost_complete = bool(self.model_usage) and len(known_costs) == len(
            self.model_usage
        )
        if self.priced_model_cost_complete != expected_cost_complete:
            raise ValueError("Foundry cost completeness is inconsistent.")

        unpriced_usage = [
            usage
            for usage in self.model_usage
            if usage.pricing_status is not FoundryModelPricingStatus.PRICED
        ]
        allowed_unpriced_usage = not unpriced_usage or (
            self.allow_service_managed_unpriced_usage
            and all(
                usage.pricing_status is FoundryModelPricingStatus.SERVICE_MANAGED_UNPRICED
                for usage in unpriced_usage
            )
        )
        within_cost = self.max_estimated_priced_model_cost_usd is None or (
            self.estimated_priced_model_cost_usd is not None
            and self.estimated_priced_model_cost_usd <= self.max_estimated_priced_model_cost_usd
        )
        expected_pass = (
            self.run_status is FoundryEvaluationRunStatus.COMPLETED
            and self.result_counts.failed == 0
            and self.result_counts.errors == 0
            and all(criterion.passes for criterion in self.criteria)
            and all(comparison.passes for comparison in self.comparisons)
            and self.duration_ms <= self.max_duration_ms
            and self.total_tokens <= self.max_total_tokens
            and within_cost
            and allowed_unpriced_usage
        )
        if self.passes != expected_pass:
            raise ValueError("Foundry certificate passes is inconsistent with its gates.")
        return self
