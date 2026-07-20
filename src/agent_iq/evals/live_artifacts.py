"""Content-safe evaluation of live invocation-bound artifact publication."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, cast

from agent_iq.contracts import (
    ARTIFACT_MEDIA_TYPE,
    ArtifactChannelReference,
    ArtifactEnvelope,
    ArtifactKind,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SAFE_METADATA = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._ -]{0,127}$")
_ARTIFACT_KINDS = {item.value for item in ArtifactKind}
_LIVE_RUN_ID = re.compile(r"^artifact-canary-run-(?P<stamp>\d{8}T\d{13}Z)-[0-9a-f]{32}$")
_LIVE_EVIDENCE_NAME = re.compile(
    r"^(?P<run_id>artifact-canary-run-\d{8}T\d{13}Z-[0-9a-f]{32})-"
    r"(?P<kind>invocation|publication|security)\.json$"
)
MAX_LIVE_EVIDENCE_AGE = timedelta(minutes=10)
MAX_LIVE_EVIDENCE_SKEW = timedelta(minutes=2)
LiveEvidenceKind = Literal["invocation", "publication", "security"]
LiveProbeMode = Literal["certification", "orchestration_response"]
_SAFE_DEPLOYMENT_KEYS = (
    "reportPath",
    "reportSha256",
    "appName",
    "resourceGroup",
    "fqdn",
    "revision",
    "imageDigest",
    "ownershipTagsSha256",
    "activeRevisionsMode",
    "authentication",
    "containerAppAuthConfig",
    "minReplicas",
    "maxReplicas",
)


@dataclass(frozen=True)
class LiveArtifactEvidence:
    """One validated, run-specific evidence object and its content hash."""

    kind: LiveEvidenceKind
    run_id: str
    generated_at: datetime
    path: Path
    sha256: str
    payload: Mapping[str, Any]

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "sha256": self.sha256,
            "generatedAt": self.generated_at.isoformat().replace("+00:00", "Z"),
        }


@dataclass(frozen=True)
class VerifiedLiveArtifactProbe:
    """A fresh HTTP transcript verified against the certified deployment state."""

    run_id: str
    correlation_id: str
    observed_at: datetime
    base_url: str
    retrieval_endpoint: str
    claim_endpoint: str
    publication_endpoint: str
    revision: str
    image_digest: str
    publication: Mapping[str, Any]
    publish_reference: Mapping[str, Any]
    retrieval_sha256: str
    publication_sha256: str
    wrong_identity_status: int
    wrong_ticket_status: int
    missing_ticket_status: int
    wrong_claim_identity_status: int
    wrong_claim_invocation_status: int
    wrong_claim_correlation_status: int
    missing_claim_credentials_status: int
    retrieval_mode: LiveProbeMode = "certification"
    invocation: Mapping[str, Any] | None = None

    def safe_metadata(self) -> dict[str, Any]:
        return {
            "observedAt": self.observed_at.isoformat().replace("+00:00", "Z"),
            "baseUrl": self.base_url,
            "retrievalMode": self.retrieval_mode,
            "retrievalEndpoint": self.retrieval_endpoint,
            "claimEndpoint": self.claim_endpoint,
            "publicationEndpoint": self.publication_endpoint,
            "revision": self.revision,
            "imageDigest": self.image_digest,
            "retrievalSha256": self.retrieval_sha256,
            "publicationSha256": self.publication_sha256,
        }


def correlation_id_for_run(run_id: str) -> str:
    """Derive the correlation nonce that binds a companion response to one run."""

    if _parse_run_id_timestamp(run_id) is None:
        raise ValueError("Live artifact run ID is malformed.")
    return f"artifact-cert-{hashlib.sha256(run_id.encode()).hexdigest()[:32]}"


def validate_live_run_binding(
    run_id: str,
    correlation_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Require a fresh explicit run name and its deterministic correlation nonce."""

    validate_live_run_freshness(run_id, now=now)
    if correlation_id != correlation_id_for_run(run_id):
        raise ValueError("Live artifact run ID and correlation ID are not fresh and bound.")


def validate_live_run_freshness(
    run_id: str,
    *,
    now: datetime | None = None,
) -> None:
    """Require a fresh explicit run name when the server owns correlation."""

    run_at = _parse_run_id_timestamp(run_id)
    reference_now = _as_utc(now or datetime.now(UTC))
    if run_at is None or not timedelta(0) <= reference_now - run_at <= MAX_LIVE_EVIDENCE_AGE:
        raise ValueError("Live artifact run ID is not fresh.")


def validate_live_publication_response(
    payload: Mapping[str, Any],
    *,
    expected_kind: str,
    correlation_id: str,
    now: datetime | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Validate the companion's actual typed publication and retrieval response."""

    if payload.get("schemaVersion") != "1.0" or payload.get("status") != "pass":
        raise ValueError("Live companion retrieval did not return a passing publication.")
    raw_reference = payload.get("reference")
    raw_envelope = payload.get("envelope")
    if (
        not isinstance(raw_reference, Mapping)
        or not isinstance(raw_envelope, Mapping)
        or raw_reference.get("schemaVersion") != "1.0"
        or raw_envelope.get("schemaVersion") != "1.0"
    ):
        raise ValueError("Live companion retrieval omitted versioned artifact contracts.")
    try:
        reference = ArtifactChannelReference.model_validate(raw_reference)
        envelope = ArtifactEnvelope.model_validate(raw_envelope)
    except ValueError as exc:
        raise ValueError(
            "Live companion retrieval returned malformed typed artifact data."
        ) from exc

    reference_payload = reference.model_dump(mode="json", by_alias=True)
    envelope_payload = envelope.model_dump(mode="json", by_alias=True)
    timestamp = _as_utc(now or datetime.now(UTC))
    artifact_payload = envelope.artifact.model_dump(mode="json", by_alias=True)
    canonical_artifact = json.dumps(
        artifact_payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    expected_hash = f"sha256:{hashlib.sha256(canonical_artifact.encode()).hexdigest()}"
    if (
        expected_kind not in _ARTIFACT_KINDS
        or reference.media_type != ARTIFACT_MEDIA_TYPE
        or reference.artifact_kind.value != expected_kind
        or envelope.artifact.kind.value != expected_kind
        or reference.artifact_id != envelope.artifact_id
        or reference.artifact_id != envelope.artifact.artifact_id
        or reference.invocation_id != envelope.invocation_id
        or reference.correlation_id != correlation_id
        or envelope.correlation_id != correlation_id
        or reference.expires_at != envelope.expires_at
        or envelope.content_hash != expected_hash
        or not timedelta(0) <= timestamp - envelope.issued_at <= MAX_LIVE_EVIDENCE_AGE
        or envelope.expires_at <= timestamp
    ):
        raise ValueError("Live companion publication is stale or not bound to this certification.")
    return reference_payload, envelope_payload


def load_live_artifact_evidence(
    path: Path,
    kind: LiveEvidenceKind,
    *,
    now: datetime | None = None,
) -> LiveArtifactEvidence:
    """Load one fresh evidence file with a run-bound name and strict schema."""

    match = _LIVE_EVIDENCE_NAME.fullmatch(path.name)
    if path.name == "latest.json" or match is None or match.group("kind") != kind:
        raise ValueError(f"{kind} evidence must be an explicit run-specific file.")
    try:
        safe_file = path.is_file() and not path.is_symlink()
    except OSError:
        safe_file = False
    if not safe_file:
        raise ValueError(f"{kind} evidence must be a regular, non-symlink file.")
    try:
        raw = path.read_bytes()
        decoded = raw.decode("utf-8-sig")
        value: object = json.loads(decoded, object_pairs_hook=_json_object_no_duplicates)
    except (OSError, UnicodeError, ValueError) as exc:
        raise ValueError(f"{kind} evidence must contain one valid JSON object.") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{kind} evidence must contain one JSON object.")
    payload = cast(dict[str, Any], value)
    run_id = match.group("run_id")
    generated_at = _parse_utc_timestamp(payload.get("generatedAt"))
    reference_now = _as_utc(now or datetime.now(UTC))
    if (
        payload.get("schemaVersion") != "1.0"
        or payload.get("status") != "pass"
        or payload.get("runId") != run_id
        or generated_at is None
        or not timedelta(0) <= reference_now - generated_at <= MAX_LIVE_EVIDENCE_AGE
        or not _has_expected_evidence_shape(payload, kind)
    ):
        raise ValueError(f"{kind} evidence failed schema, status, identity, or freshness checks.")
    return LiveArtifactEvidence(
        kind=kind,
        run_id=run_id,
        generated_at=generated_at,
        path=path.resolve(),
        sha256=f"sha256:{hashlib.sha256(raw).hexdigest()}",
        payload=payload,
    )


def validate_live_artifact_evidence(
    invocation: LiveArtifactEvidence,
    publication: LiveArtifactEvidence,
    security: LiveArtifactEvidence,
) -> tuple[str, dict[str, dict[str, Any]]]:
    """Require one coherent run across all independently captured evidence."""

    evidence = (invocation, publication, security)
    if tuple(item.kind for item in evidence) != ("invocation", "publication", "security"):
        raise ValueError("Live artifact evidence kinds are incomplete or out of order.")
    run_ids = {item.run_id for item in evidence}
    timestamps = [item.generated_at for item in evidence]
    invocation_id = invocation.payload.get("invocationId")
    correlation_id = invocation.payload.get("correlationId")
    if (
        len(run_ids) != 1
        or max(timestamps) - min(timestamps) > MAX_LIVE_EVIDENCE_SKEW
        or security.payload.get("invocationId") != invocation_id
        or security.payload.get("correlationId") != correlation_id
    ):
        raise ValueError("Live artifact evidence does not identify one coherent run.")
    return invocation.run_id, {item.kind: item.safe_metadata() for item in evidence}


def build_live_artifact_payload(
    invocation: Mapping[str, Any],
    publication: Mapping[str, Any],
    security: Mapping[str, Any],
    *,
    expected_kind: str,
    companion_image_digest: str,
    generated_at: datetime | None = None,
    evidence_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    deployment_metadata: Mapping[str, Any] | None = None,
    live_probe: VerifiedLiveArtifactProbe | None = None,
) -> dict[str, Any]:
    """Build a content-safe certificate that cannot pass without a verified live probe."""

    authoritative_publication = live_probe.publication if live_probe is not None else publication
    reference = _mapping(authoritative_publication.get("reference"), "publication reference")
    envelope = _mapping(authoritative_publication.get("envelope"), "artifact envelope")
    producer = _mapping(envelope.get("producer"), "artifact producer")
    artifact = _mapping(envelope.get("artifact"), "business artifact")
    serialized = json.dumps(
        {
            "invocation": invocation,
            "publication": authoritative_publication,
            "security": security,
        },
        sort_keys=True,
    )
    invocation_id = envelope.get("invocationId")
    correlation_id = envelope.get("correlationId")
    artifact_id = artifact.get("artifactId")
    identity_id = producer.get("identityId")
    content_hash = envelope.get("contentHash")
    evidence_ids = artifact.get("evidenceIds")
    timestamp = _as_utc(generated_at or datetime.now(UTC))
    evidence_timestamps = [
        _parse_utc_timestamp(item.get("generatedAt"))
        for item in (invocation, publication, security)
    ]
    run_ids = [item.get("runId") for item in (invocation, publication, security)]
    live_semantics = False
    if live_probe is not None:
        try:
            validated_reference, _ = validate_live_publication_response(
                live_probe.publication,
                expected_kind=expected_kind,
                correlation_id=live_probe.correlation_id,
                now=timestamp,
            )
            live_semantics = validated_reference == dict(live_probe.publish_reference)
        except ValueError:
            live_semantics = False
    run_binding = False
    if live_probe is not None:
        try:
            if live_probe.retrieval_mode == "orchestration_response":
                validate_live_run_freshness(live_probe.run_id, now=timestamp)
            else:
                validate_live_run_binding(
                    live_probe.run_id,
                    live_probe.correlation_id,
                    now=timestamp,
                )
            run_binding = (
                live_probe.run_id == run_ids[0] == run_ids[1] == run_ids[2]
                and live_probe.correlation_id == correlation_id
            )
        except ValueError:
            run_binding = False
    deployment_binding = (
        live_probe is not None
        and deployment_metadata is not None
        and live_probe.revision == deployment_metadata.get("revision")
        and live_probe.image_digest == companion_image_digest
        and live_probe.image_digest == deployment_metadata.get("imageDigest")
        and live_probe.base_url.casefold() == _canonical_origin(deployment_metadata.get("fqdn"))
    )

    checks = {
        "liveProbe.performed": live_probe is not None,
        "liveProbe.fresh": (
            live_probe is not None
            and timedelta(0)
            <= timestamp - _as_utc(live_probe.observed_at)
            <= MAX_LIVE_EVIDENCE_SKEW
        ),
        "liveProbe.runCorrelationBound": run_binding,
        "liveProbe.deploymentBound": deployment_binding,
        "liveProbe.publicationAuthenticated": (
            live_probe is not None
            and _SHA256.fullmatch(live_probe.publication_sha256) is not None
            and live_semantics
        ),
        "liveProbe.retrievalAuthenticated": (
            live_probe is not None
            and _SHA256.fullmatch(live_probe.retrieval_sha256) is not None
            and live_semantics
        ),
        "liveProbe.securityBoundary": (
            live_probe is not None
            and live_probe.wrong_identity_status == 403
            and live_probe.wrong_ticket_status == 403
            and live_probe.missing_ticket_status == 403
            and live_probe.wrong_claim_identity_status == 403
            and live_probe.wrong_claim_invocation_status == 403
            and live_probe.wrong_claim_correlation_status == 403
            and live_probe.missing_claim_credentials_status == 401
        ),
        "evidence.schemas": all(
            item.get("schemaVersion") == "1.0" for item in (invocation, publication, security)
        ),
        "evidence.statuses": all(
            item.get("status") == "pass" for item in (invocation, publication, security)
        ),
        "evidence.shapes": (
            _has_expected_evidence_shape(invocation, "invocation")
            and _has_expected_evidence_shape(publication, "publication")
            and _has_expected_evidence_shape(security, "security")
        ),
        "evidence.fresh": all(
            observed is not None and timedelta(0) <= timestamp - observed <= MAX_LIVE_EVIDENCE_AGE
            for observed in evidence_timestamps
        ),
        "binding.runIdentity": (
            _is_nonempty_string(run_ids[0])
            and run_ids[0] == run_ids[1]
            and run_ids[1] == run_ids[2]
        ),
        "invocation.passed": invocation.get("status") == "pass",
        "invocation.nativeA2AV1": invocation.get("protocolVersion") == "1.0",
        "invocation.answerNonempty": invocation.get("answerNonempty") is True,
        "invocation.ticketNotPersisted": (invocation.get("publicationTicketPersisted") is False),
        "publication.passed": authoritative_publication.get("status") == "pass",
        "publication.typedResponseValidated": live_semantics,
        "binding.invocation": (
            isinstance(invocation_id, str)
            and invocation.get("invocationId") == invocation_id
            and reference.get("invocationId") == invocation_id
            and envelope.get("invocationId") == invocation_id
        ),
        "binding.correlation": (
            isinstance(correlation_id, str)
            and invocation.get("correlationId") == correlation_id
            and reference.get("correlationId") == correlation_id
            and envelope.get("correlationId") == correlation_id
        ),
        "binding.security": (
            security.get("invocationId") == invocation_id
            and security.get("correlationId") == correlation_id
        ),
        "binding.producer": (
            producer.get("agentId") == invocation.get("agentId")
            and producer.get("agentVersion") == invocation.get("agentVersion")
        ),
        "artifact.kind": (
            artifact.get("kind") == expected_kind and reference.get("artifactKind") == expected_kind
        ),
        "artifact.identity": (
            isinstance(artifact_id, str)
            and reference.get("artifactId") == artifact_id
            and envelope.get("artifactId") == artifact_id
        ),
        "artifact.contentHash": isinstance(content_hash, str)
        and _SHA256.fullmatch(content_hash) is not None,
        "security.wrongIdentityDenied": (
            live_probe is not None and live_probe.wrong_identity_status == 403
        ),
        "security.wrongTicketDenied": (
            live_probe is not None and live_probe.wrong_ticket_status == 403
        ),
        "security.missingTicketDenied": (
            live_probe is not None and live_probe.missing_ticket_status == 403
        ),
        "security.wrongClaimIdentityDenied": (
            live_probe is not None and live_probe.wrong_claim_identity_status == 403
        ),
        "security.wrongClaimInvocationDenied": (
            live_probe is not None and live_probe.wrong_claim_invocation_status == 403
        ),
        "security.wrongClaimCorrelationDenied": (
            live_probe is not None and live_probe.wrong_claim_correlation_status == 403
        ),
        "security.missingClaimCredentialsDenied": (
            live_probe is not None and live_probe.missing_claim_credentials_status == 401
        ),
        "security.noCredentialPersisted": (
            "apt-" not in serialized and "Bearer " not in serialized and "eyJ" not in serialized
        ),
        "deployment.imagePinned": _SHA256.fullmatch(companion_image_digest) is not None,
    }
    failures = [name for name, passed in checks.items() if not passed]
    run_id = live_probe.run_id if live_probe is not None else None
    payload: dict[str, Any] = {
        "schemaVersion": "1.0",
        "suiteId": "foundry-artifact-publication-live",
        "status": "pass" if not failures else "fail",
        "generatedAt": timestamp.isoformat().replace("+00:00", "Z"),
        "runIdSha256": _identifier_hash(run_id),
        "transport": {
            "southbound": "native Foundry A2A",
            "protocolVersion": ("1.0" if invocation.get("protocolVersion") == "1.0" else None),
            "publication": "Entra-authenticated companion HTTP",
            "fallbackUsed": False,
        },
        "producer": {
            "agentId": _safe_metadata_value(producer.get("agentId")),
            "agentVersion": _safe_metadata_value(producer.get("agentVersion")),
            "framework": _safe_metadata_value(producer.get("framework")),
            "identitySha256": (
                f"sha256:{hashlib.sha256(identity_id.encode()).hexdigest()}"
                if isinstance(identity_id, str)
                else None
            ),
        },
        "invocation": {
            "invocationIdSha256": _identifier_hash(invocation_id),
            "correlationIdSha256": _identifier_hash(correlation_id),
            "durationMs": _safe_nonnegative_int(invocation.get("durationMs")),
            "answerHash": _safe_digest(invocation.get("answerHash")),
        },
        "artifact": {
            "artifactIdSha256": _identifier_hash(artifact_id),
            "kind": artifact.get("kind") if artifact.get("kind") in _ARTIFACT_KINDS else None,
            "contentHash": _safe_digest(content_hash),
            "evidenceCount": len(evidence_ids) if isinstance(evidence_ids, list) else None,
        },
        "security": {
            "wrongIdentityStatus": _safe_http_status(
                live_probe.wrong_identity_status if live_probe is not None else None
            ),
            "wrongTicketStatus": _safe_http_status(
                live_probe.wrong_ticket_status if live_probe is not None else None
            ),
            "missingTicketStatus": _safe_http_status(
                live_probe.missing_ticket_status if live_probe is not None else None
            ),
            "wrongClaimIdentityStatus": _safe_http_status(
                live_probe.wrong_claim_identity_status if live_probe is not None else None
            ),
            "wrongClaimInvocationStatus": _safe_http_status(
                live_probe.wrong_claim_invocation_status if live_probe is not None else None
            ),
            "wrongClaimCorrelationStatus": _safe_http_status(
                live_probe.wrong_claim_correlation_status if live_probe is not None else None
            ),
            "missingClaimCredentialsStatus": _safe_http_status(
                live_probe.missing_claim_credentials_status if live_probe is not None else None
            ),
        },
        "companionImageDigest": _safe_digest(companion_image_digest),
        "checks": checks,
        "failures": failures,
    }
    if evidence_metadata is not None:
        payload["evidence"] = {
            kind: {
                key: metadata.get(key)
                for key in ("path", "sha256", "generatedAt")
                if key in metadata
            }
            for kind, metadata in evidence_metadata.items()
            if kind in {"invocation", "publication", "security"}
        }
    if deployment_metadata is not None:
        payload["deployment"] = {
            key: deployment_metadata[key]
            for key in _SAFE_DEPLOYMENT_KEYS
            if key in deployment_metadata
        }
    if live_probe is not None:
        payload["liveProbe"] = live_probe.safe_metadata()
    return payload


def _has_expected_evidence_shape(
    payload: Mapping[str, Any],
    kind: LiveEvidenceKind,
) -> bool:
    if kind == "invocation":
        duration_ms = payload.get("durationMs")
        return (
            _is_nonempty_string(payload.get("agentId"))
            and _is_nonempty_string(payload.get("agentVersion"))
            and _is_nonempty_string(payload.get("invocationId"))
            and _is_nonempty_string(payload.get("correlationId"))
            and payload.get("protocolVersion") == "1.0"
            and isinstance(duration_ms, int)
            and not isinstance(duration_ms, bool)
            and duration_ms >= 0
            and isinstance(payload.get("answerHash"), str)
            and _SHA256.fullmatch(str(payload.get("answerHash"))) is not None
            and payload.get("answerNonempty") is True
            and payload.get("publicationTicketPersisted") is False
        )
    if kind == "publication":
        reference = payload.get("reference")
        envelope = payload.get("envelope")
        checks = payload.get("checks")
        if not all(isinstance(item, Mapping) for item in (reference, envelope, checks)):
            return False
        assert isinstance(reference, Mapping)
        assert isinstance(envelope, Mapping)
        producer = envelope.get("producer")
        artifact = envelope.get("artifact")
        return (
            isinstance(producer, Mapping)
            and isinstance(artifact, Mapping)
            and _is_nonempty_string(reference.get("artifactId"))
            and reference.get("artifactKind") in _ARTIFACT_KINDS
            and _is_nonempty_string(reference.get("invocationId"))
            and _is_nonempty_string(reference.get("correlationId"))
            and _is_nonempty_string(envelope.get("artifactId"))
            and _is_nonempty_string(envelope.get("invocationId"))
            and _is_nonempty_string(envelope.get("correlationId"))
            and isinstance(envelope.get("contentHash"), str)
            and _SHA256.fullmatch(str(envelope.get("contentHash"))) is not None
            and _safe_metadata_value(producer.get("agentId")) is not None
            and _safe_metadata_value(producer.get("agentVersion")) is not None
            and _is_nonempty_string(producer.get("identityId"))
            and _safe_metadata_value(producer.get("framework")) is not None
            and _is_nonempty_string(artifact.get("artifactId"))
            and artifact.get("kind") in _ARTIFACT_KINDS
            and isinstance(artifact.get("evidenceIds"), list)
        )
    return (
        _is_nonempty_string(payload.get("invocationId"))
        and _is_nonempty_string(payload.get("correlationId"))
        and payload.get("wrongIdentityStatus") == 403
        and payload.get("wrongTicketStatus") == 403
        and payload.get("missingTicketStatus") == 403
        and payload.get("wrongClaimIdentityStatus") == 403
        and payload.get("wrongClaimInvocationStatus") == 403
        and payload.get("wrongClaimCorrelationStatus") == 403
        and payload.get("missingClaimCredentialsStatus") == 401
        and payload.get("emptyGrantStatus") == 404
    )


def _parse_utc_timestamp(value: object) -> datetime | None:
    if not _is_nonempty_string(value):
        return None
    timestamp = str(value)
    if timestamp.endswith("Z"):
        timestamp = f"{timestamp[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(timestamp)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(UTC)


def _parse_run_id_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    match = _LIVE_RUN_ID.fullmatch(value)
    if match is None:
        return None
    stamp = match.group("stamp")
    digits = stamp[9:-1]
    try:
        parsed = datetime.strptime(
            f"{stamp[:8]}{digits[:6]}{digits[6:12]}",
            "%Y%m%d%H%M%S%f",
        )
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC)


def _canonical_origin(value: object) -> str | None:
    if not isinstance(value, str) or not value or value != value.strip():
        return None
    origin = value[:-1] if value.endswith("/") else value
    if not origin.startswith("https://"):
        origin = f"https://{origin}"
    if "/" in origin[len("https://") :]:
        return None
    return origin.casefold()


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _is_nonempty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _identifier_hash(value: object) -> str | None:
    if not _is_nonempty_string(value):
        return None
    return f"sha256:{hashlib.sha256(str(value).encode()).hexdigest()}"


def _safe_metadata_value(value: object) -> str | None:
    return str(value) if isinstance(value, str) and _SAFE_METADATA.fullmatch(value) else None


def _safe_digest(value: object) -> str | None:
    return str(value) if isinstance(value, str) and _SHA256.fullmatch(value) else None


def _safe_nonnegative_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _safe_http_status(value: object) -> int | None:
    return (
        value
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599
        else None
    )


def _json_object_no_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key.")
        result[key] = value
    return result


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"The {name} is missing or malformed.")
    return value
