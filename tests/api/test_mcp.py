from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import CallToolResult
from pydantic import AnyUrl

from agent_iq.api import AgentIqService, create_mcp_server
from agent_iq.api.mcp import _progress_update
from agent_iq.api.mcp_apps import (
    EMPLOYEE_OPERATIONS_MIME_TYPE,
    EMPLOYEE_OPERATIONS_RESOURCE_URI,
    FORMATION_MIME_TYPE,
    FORMATION_RESOURCE_URI,
    HISTORICAL_RESOURCE_TEMPLATE_URI,
    LEGACY_VACATION_RING_RESOURCE_URI,
    VACATION_RING_MIME_TYPE,
    VACATION_RING_RESOURCE_URI,
)
from agent_iq.api.presentation import public_text
from agent_iq.catalog import LiveDemoInventory
from agent_iq.contracts import (
    VACATION_BALANCE_JSON_END,
    VACATION_BALANCE_JSON_START,
    VPN_RECOVERY_JSON_END,
    VPN_RECOVERY_JSON_START,
    AgentSearchPresentation,
    ExecutionSnapshot,
    FeedbackRating,
    FeedbackSubmission,
    OrchestrationCommand,
    OrchestrationPresentation,
    SquadAuthorizedPayload,
    SquadOverriddenPayload,
    SquadProposedPayload,
)
from agent_iq.employee import (
    EmployeeActionReceipt,
    EmployeeExperiencePresentation,
    EmployeeScenarioCatalogPresentation,
    VacationBalancePresentation,
)


def test_mcp_exposes_thirteen_tools_and_reports_lifecycle_progress(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        async def vacation_preflight() -> LiveDemoInventory:
            return LiveDemoInventory(active_project_agents=50, indexed_candidates=23)

        server = create_mcp_server(
            service_factory(),
            vacation_demo_preflight=vacation_preflight,
        )
        progress_events: list[str] = []
        progress_totals: list[float | None] = []

        async def capture_progress(
            progress: float,
            total: float | None,
            message: str | None,
        ) -> None:
            del progress
            progress_totals.append(total)
            if message is not None:
                progress_events.append(message)

        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            listed = await session.list_tools()
            listed_resources = await session.list_resources()
            listed_templates = await session.list_resource_templates()
            assert [tool.name for tool in listed.tools] == [
                "search_agents",
                "orchestrate",
                "run_single_agent_demo",
                "check_vacation_balance",
                "run_vacation_balance_demo",
                "get_execution",
                "cancel_execution",
                "submit_feedback",
                "capabilities",
                "list_employee_scenarios",
                "resolve_employee_intent",
                "open_employee_experience",
                "prepare_employee_action",
            ]
            formation_meta = {
                "ui": {
                    "resourceUri": FORMATION_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            }
            tools = {tool.name: tool for tool in listed.tools}
            assert tools["search_agents"].meta == formation_meta
            assert tools["orchestrate"].meta == formation_meta
            assert tools["run_single_agent_demo"].meta == formation_meta
            assert "real Agent IQ catalog discovery" in (
                tools["run_single_agent_demo"].description or ""
            )
            assert [str(resource.uri) for resource in listed_resources.resources] == [
                FORMATION_RESOURCE_URI,
                VACATION_RING_RESOURCE_URI,
                LEGACY_VACATION_RING_RESOURCE_URI,
                EMPLOYEE_OPERATIONS_RESOURCE_URI,
            ]
            assert [
                str(resource.uriTemplate) for resource in listed_templates.resourceTemplates
            ] == [HISTORICAL_RESOURCE_TEMPLATE_URI]
            formation_resource = listed_resources.resources[0]
            assert formation_resource.model_dump(by_alias=True)["mimeType"] == FORMATION_MIME_TYPE
            assert formation_resource.model_dump(by_alias=True)["_meta"]["ui"] == {
                "csp": {
                    "connectDomains": [],
                    "resourceDomains": [],
                    "frameDomains": [],
                    "baseUriDomains": [],
                },
                "prefersBorder": False,
            }
            formation_read = await session.read_resource(formation_resource.uri)
            assert len(formation_read.contents) == 1
            assert (
                formation_read.contents[0].model_dump(by_alias=True)["mimeType"]
                == FORMATION_MIME_TYPE
            )
            assert (
                formation_read.contents[0].model_dump(by_alias=True)["_meta"]
                == (formation_resource.model_dump(by_alias=True)["_meta"])
            )
            assert getattr(formation_read.contents[0], "text", "").startswith("<!doctype html>")
            vacation_resource = listed_resources.resources[1]
            assert (
                vacation_resource.model_dump(by_alias=True)["mimeType"] == VACATION_RING_MIME_TYPE
            )
            assert (
                vacation_resource.model_dump(by_alias=True)["_meta"]
                == (formation_resource.model_dump(by_alias=True)["_meta"])
            )
            vacation_read = await session.read_resource(vacation_resource.uri)
            assert getattr(vacation_read.contents[0], "text", "").startswith("<!doctype html>")
            legacy_vacation_resource = listed_resources.resources[2]
            legacy_vacation_read = await session.read_resource(legacy_vacation_resource.uri)
            assert getattr(legacy_vacation_read.contents[0], "text", "") == getattr(
                vacation_read.contents[0], "text", ""
            )
            employee_resource = listed_resources.resources[3]
            assert (
                employee_resource.model_dump(by_alias=True)["mimeType"]
                == EMPLOYEE_OPERATIONS_MIME_TYPE
            )
            assert (
                employee_resource.model_dump(by_alias=True)["_meta"]
                == (formation_resource.model_dump(by_alias=True)["_meta"])
            )
            employee_read = await session.read_resource(employee_resource.uri)
            assert getattr(employee_read.contents[0], "text", "").startswith("<!doctype html>")
            historical_read = await session.read_resource(
                AnyUrl("ui://agent-iq/retired-widget.html")
            )
            assert "Historical Agent IQ experience" in getattr(
                historical_read.contents[0], "text", ""
            )
            public_metadata = " ".join(
                f"{tool.title or ''} {tool.description or ''}" for tool in listed.tools
            ).casefold()
            public_resources = " ".join(
                getattr(read.contents[0], "text", "")
                for read in (
                    formation_read,
                    vacation_read,
                    legacy_vacation_read,
                    employee_read,
                    historical_read,
                )
            ).casefold()
            for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
                assert forbidden_label not in public_metadata
                assert forbidden_label not in public_resources
            vacation_meta = {
                "ui": {
                    "resourceUri": VACATION_RING_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            }
            assert tools["check_vacation_balance"].meta == vacation_meta
            assert tools["run_vacation_balance_demo"].meta == vacation_meta
            employee_meta = {
                "ui": {
                    "resourceUri": EMPLOYEE_OPERATIONS_RESOURCE_URI,
                    "visibility": ["model", "app"],
                }
            }
            for name in (
                "list_employee_scenarios",
                "resolve_employee_intent",
                "open_employee_experience",
                "prepare_employee_action",
            ):
                assert tools[name].meta == employee_meta
            annotations = {tool.name: tool.annotations for tool in listed.tools}
            assert annotations["search_agents"] is not None
            assert annotations["search_agents"].readOnlyHint
            assert not annotations["search_agents"].destructiveHint
            assert annotations["orchestrate"] is not None
            assert annotations["orchestrate"].readOnlyHint
            assert not annotations["orchestrate"].destructiveHint
            assert annotations["orchestrate"].openWorldHint
            assert annotations["run_single_agent_demo"] is not None
            assert annotations["run_single_agent_demo"].readOnlyHint
            assert annotations["run_single_agent_demo"].openWorldHint
            assert annotations["check_vacation_balance"] is not None
            assert annotations["check_vacation_balance"].readOnlyHint
            assert annotations["check_vacation_balance"].openWorldHint
            assert annotations["run_vacation_balance_demo"] is not None
            assert annotations["run_vacation_balance_demo"].readOnlyHint
            assert annotations["run_vacation_balance_demo"].openWorldHint
            assert annotations["get_execution"] is not None
            assert annotations["get_execution"].readOnlyHint
            assert annotations["capabilities"] is not None
            assert annotations["capabilities"].readOnlyHint
            assert annotations["cancel_execution"] is not None
            assert not annotations["cancel_execution"].readOnlyHint
            assert annotations["cancel_execution"].destructiveHint
            assert annotations["cancel_execution"].idempotentHint
            assert annotations["submit_feedback"] is not None
            assert not annotations["submit_feedback"].readOnlyHint
            assert not annotations["submit_feedback"].destructiveHint
            assert annotations["submit_feedback"].idempotentHint
            for name in (
                "list_employee_scenarios",
                "resolve_employee_intent",
                "open_employee_experience",
                "prepare_employee_action",
            ):
                annotation = annotations[name]
                assert annotation is not None
                assert annotation.readOnlyHint
                assert not annotation.destructiveHint
                assert annotation.idempotentHint
            called = await session.call_tool(
                "orchestrate",
                {
                    "command": orchestration_command.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                },
                progress_callback=capture_progress,
            )
            single_demo = await session.call_tool("run_single_agent_demo")
            vacation_demo = await session.call_tool("check_vacation_balance")
            legacy_vacation = await session.call_tool("run_vacation_balance_demo")
            vpn_command = orchestration_command.model_copy(
                update={
                    "request": orchestration_command.request.model_copy(
                        update={
                            "request_id": "request-vpn-1",
                            "intent": "My VPN is down",
                        }
                    )
                }
            )
            vpn_result = await session.call_tool(
                "orchestrate",
                {"command": vpn_command.model_dump(mode="json", by_alias=True)},
            )

        assert not called.isError
        result = OrchestrationPresentation.model_validate(called.structuredContent)
        assert [reason.agent_id for reason in result.selected_agents] == ["consumer-formulation-rd"]
        assert result.citations[0].excerpt == (
            "The package uses protease, amylase, mannanase, and lipase."
        )
        assert not single_demo.isError
        demo_result = OrchestrationPresentation.model_validate(single_demo.structuredContent)
        assert [reason.agent_id for reason in demo_result.selected_agents] == [
            "consumer-formulation-rd"
        ]
        assert demo_result.status.value == "completed"
        assert [citation.source_id for citation in demo_result.citations] == [
            "consumer-brightwash-cold",
            "consumer-clearwave-cold",
        ]
        assert all(citation.excerpt for citation in demo_result.citations)
        assert not vacation_demo.isError
        vacation_result = VacationBalancePresentation.model_validate(
            vacation_demo.structuredContent
        )
        assert [agent.agent_id for agent in vacation_result.selected_agents] == [
            "employee-vacation-leave"
        ]
        assert vacation_result.title == "12 vacation days remaining"
        assert vacation_result.presentation_style == "compact_ring"
        assert vacation_result.remaining_days == 12
        assert vacation_result.entitlement_days == 25
        assert vacation_result.taken_days == 10
        assert vacation_result.booked_days == 3
        assert len(vacation_result.sources) == 1
        assert vacation_result.sources[0].title == "Vacation balance 2026"
        assert vacation_result.status.value == "completed"
        assert not vpn_result.isError
        vpn_presentation = OrchestrationPresentation.model_validate(vpn_result.structuredContent)
        assert [agent.agent_id for agent in vpn_presentation.selected_agents] == [
            "employee-it-helpdesk"
        ]
        assert vpn_presentation.vpn_recovery is not None
        assert vpn_presentation.vpn_recovery.status == "available"
        assert vpn_presentation.vpn_recovery.source is not None
        assert vpn_presentation.vpn_recovery.source.source_id == "employee-vpn-recovery-guidance"
        assert "reconnect the vpn once" in vpn_presentation.answer.casefold()
        vpn_payload = json.dumps(vpn_result.structuredContent).casefold()
        for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
            assert forbidden_label not in vpn_payload
        assert _payload_bytes(vpn_result) <= 16 * 1024
        assert not legacy_vacation.isError
        legacy_vacation_result = VacationBalancePresentation.model_validate(
            legacy_vacation.structuredContent
        )
        assert legacy_vacation_result.title == vacation_result.title
        assert legacy_vacation_result.remaining_days == vacation_result.remaining_days
        vacation_payload = json.dumps(vacation_demo.structuredContent).casefold()
        for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
            assert forbidden_label not in vacation_payload
        orchestration_payload = json.dumps(single_demo.structuredContent).casefold()
        for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
            assert forbidden_label not in orchestration_payload
        assert progress_events == [
            "Matching eligible specialists",
            (
                "Selected Fabrikam Consumer Products Formulation R&D - matched "
                "formulation-science, stability-costing, enzymes, brightwash"
            ),
            "Prepared the governed execution plan",
            "Retrieving approved evidence from Fabrikam Consumer Products Formulation R&D",
            "Building the cited answer",
            "Completed",
        ]
        assert progress_totals == [100.0] * len(progress_events)
        assert _payload_bytes(called) <= 16 * 1024

    asyncio.run(run())


def test_orchestrate_returns_vpn_unavailable_when_authenticated_guidance_is_malformed(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory(malformed_vpn_payload=True))
        vpn_command = orchestration_command.model_copy(
            update={
                "request": orchestration_command.request.model_copy(
                    update={
                        "request_id": "request-vpn-malformed",
                        "intent": "My VPN is down",
                    }
                )
            }
        )
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            called = await session.call_tool(
                "orchestrate",
                {"command": vpn_command.model_dump(mode="json", by_alias=True)},
            )

        assert not called.isError
        result = OrchestrationPresentation.model_validate(called.structuredContent)
        assert result.vpn_recovery is not None
        assert result.vpn_recovery.status == "unavailable"
        assert result.answer == "Authenticated VPN guidance was unavailable for this run."
        assert any(issue.agent_id == "employee-it-helpdesk" for issue in result.issues)

    asyncio.run(run())


def test_orchestrate_does_not_rewrite_remote_access_escalation_as_vpn_recovery(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory())
        escalation_command = orchestration_command.model_copy(
            update={
                "request": orchestration_command.request.model_copy(
                    update={
                        "request_id": "request-remote-access-escalation",
                        "intent": "When do I escalate a remote access issue?",
                    }
                )
            }
        )
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            called = await session.call_tool(
                "orchestrate",
                {"command": escalation_command.model_dump(mode="json", by_alias=True)},
            )

        assert not called.isError
        result = OrchestrationPresentation.model_validate(called.structuredContent)
        assert [agent.agent_id for agent in result.selected_agents] == ["employee-it-helpdesk"]
        assert "remote-access-escalation" in result.selected_agents[0].matched_skills
        assert result.vpn_recovery is None
        assert "2,400 resolved VPN recoveries" not in result.answer
        assert "reconnecting the VPN once before escalating" in result.answer
        public_payload = json.dumps(called.structuredContent)
        assert "VPN_RECOVERY_JSON_START" not in public_payload
        assert "VPN_RECOVERY_JSON_END" not in public_payload

    asyncio.run(run())


def test_orchestrate_keeps_compact_vpn_ui_when_both_it_skills_match(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory())
        vpn_command = orchestration_command.model_copy(
            update={
                "request": orchestration_command.request.model_copy(
                    update={
                        "request_id": "request-vpn-overlapping-skills",
                        "intent": "VPN remote access issue",
                    }
                )
            }
        )
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            called = await session.call_tool(
                "orchestrate",
                {"command": vpn_command.model_dump(mode="json", by_alias=True)},
            )

        assert not called.isError
        result = OrchestrationPresentation.model_validate(called.structuredContent)
        assert result.selected_agents[0].matched_skills == (
            "vpn-recovery-guidance",
            "remote-access-escalation",
        )
        assert result.vpn_recovery is not None
        assert result.vpn_recovery.status == "available"

    asyncio.run(run())


def test_compact_vpn_ui_selects_marked_guidance_from_multi_source_evidence(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory(extra_employee_evidence=True))
        vpn_command = orchestration_command.model_copy(
            update={
                "request": orchestration_command.request.model_copy(
                    update={
                        "request_id": "request-vpn-multi-source",
                        "intent": "My VPN is down",
                    }
                )
            }
        )
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            called = await session.call_tool(
                "orchestrate",
                {"command": vpn_command.model_dump(mode="json", by_alias=True)},
            )

        assert not called.isError
        result = OrchestrationPresentation.model_validate(called.structuredContent)
        assert len(result.citations) == 2
        assert result.vpn_recovery is not None
        assert result.vpn_recovery.status == "available"
        assert result.vpn_recovery.source is not None
        assert result.vpn_recovery.source.source_id == "employee-vpn-recovery-guidance"

    asyncio.run(run())


def test_vacation_ring_selects_marked_balance_from_multi_source_evidence(
    service_factory: Callable[..., AgentIqService],
) -> None:
    async def run() -> None:
        async def vacation_preflight() -> LiveDemoInventory:
            return LiveDemoInventory(active_project_agents=50, indexed_candidates=23)

        server = create_mcp_server(
            service_factory(extra_employee_evidence=True),
            vacation_demo_preflight=vacation_preflight,
        )
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            called = await session.call_tool(
                "check_vacation_balance",
                {"question": "How much vacation do I have remaining this year?"},
            )

        assert not called.isError
        result = VacationBalancePresentation.model_validate(called.structuredContent)
        assert result.remaining_days == 12
        assert len(result.sources) == 1
        assert result.sources[0].source_id == "employee-vacation-balance-2026"

    asyncio.run(run())


def test_public_service_strips_machine_payloads_but_trusted_presentation_keeps_them(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        service = service_factory()
        vpn_command = orchestration_command.model_copy(
            update={
                "request": orchestration_command.request.model_copy(
                    update={
                        "request_id": "request-vpn-public-redaction",
                        "intent": "My VPN is down",
                    }
                )
            }
        )
        started = await service.start(vpn_command)
        internal = await service.wait_for_presentation(started.execution_id)
        public = await service.wait(started.execution_id)
        snapshot = await service.get_execution(started.execution_id)

        internal_payload = internal.model_dump_json()
        public_payload = public.model_dump_json()
        snapshot_payload = snapshot.model_dump_json()
        for marker in (
            VPN_RECOVERY_JSON_START,
            VPN_RECOVERY_JSON_END,
            VACATION_BALANCE_JSON_START,
            VACATION_BALANCE_JSON_END,
        ):
            if marker.startswith("VPN"):
                assert marker in internal_payload
            assert marker not in public_payload
            assert marker not in snapshot_payload

    asyncio.run(run())


def test_public_text_fails_closed_for_incomplete_machine_payloads() -> None:
    machine_body = '{"machineOnlyField":"must-not-escape"}'
    for start_marker, end_marker in (
        (VPN_RECOVERY_JSON_START, VPN_RECOVERY_JSON_END),
        (VACATION_BALANCE_JSON_START, VACATION_BALANCE_JSON_END),
    ):
        missing_end = f"Human guidance.\n{start_marker}\n{machine_body}"
        missing_start = f"{machine_body}\n{end_marker}\nHuman guidance."

        assert public_text(missing_end).strip() == "Human guidance."
        assert public_text(missing_start).strip() == "Human guidance."
        assert machine_body not in public_text(missing_end)
        assert machine_body not in public_text(missing_start)
        assert public_text(f"{start_marker}\n{machine_body}\n{end_marker}") == (
            "The selected agent returned no human-readable guidance."
        )


def test_mcp_auxiliary_tools_share_the_same_execution_ledger(
    service_factory: Callable[..., AgentIqService],
    orchestration_command: OrchestrationCommand,
) -> None:
    async def run() -> None:
        service = service_factory()
        server = create_mcp_server(service)
        command = orchestration_command.model_dump(mode="json", by_alias=True)
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()
            capabilities = await session.call_tool("capabilities")
            assert capabilities.structuredContent is not None
            assert capabilities.structuredContent["product"] == "Agent IQ"
            searched = await session.call_tool("search_agents", {"command": command})
            search = AgentSearchPresentation.model_validate(searched.structuredContent)
            assert search.candidates[0].agent_id == "consumer-formulation-rd"
            assert len(search.candidates) == 5
            assert search.total_candidates == 20
            assert _payload_bytes(searched) <= 12 * 1024

            called = await session.call_tool("orchestrate", {"command": command})
            result = OrchestrationPresentation.model_validate(called.structuredContent)
            fetched = await session.call_tool(
                "get_execution",
                {"execution_id": result.execution_id},
            )
            snapshot = ExecutionSnapshot.model_validate(fetched.structuredContent)
            assert snapshot.status.value == "completed"
            assert snapshot.summary is not None
            assert snapshot.events[-1].payload.type == "execution.completed"

            feedback = FeedbackSubmission(
                feedback_id="feedback-mcp-1",
                execution_id=result.execution_id,
                subject_id="caller-1",
                tenant_id="tenant-1",
                rating=FeedbackRating.HELPFUL,
                submitted_at=datetime.now(UTC),
            )
            receipt = await session.call_tool(
                "submit_feedback",
                {
                    "submission": feedback.model_dump(
                        mode="json",
                        by_alias=True,
                    )
                },
            )
            assert receipt.structuredContent is not None
            assert receipt.structuredContent["accepted"]

        slow_service = service_factory(delay_seconds=10.0)
        started = await slow_service.start(orchestration_command)
        async for event in slow_service.watch_events(started.execution_id):
            if event.payload.type == "invocation.started":
                break
        slow_server = create_mcp_server(slow_service)
        async with create_connected_server_and_client_session(slow_server) as session:
            await session.initialize()
            cancelled = await session.call_tool(
                "cancel_execution",
                {"execution_id": started.execution_id},
            )
            assert cancelled.structuredContent is not None
            assert cancelled.structuredContent["status"] == "running"
        cancelled_result = await slow_service.wait(started.execution_id)
        assert cancelled_result.summary.status.value == "cancelled"

    asyncio.run(run())


def test_mcp_employee_showcase_tools_resolve_and_prepare_without_execution(
    service_factory: Callable[..., AgentIqService],
) -> None:
    async def run() -> None:
        server = create_mcp_server(service_factory())
        async with create_connected_server_and_client_session(server) as session:
            await session.initialize()

            catalog_result = await session.call_tool("list_employee_scenarios")
            catalog = EmployeeScenarioCatalogPresentation.model_validate(
                catalog_result.structuredContent
            )
            assert len(catalog.scenarios) == 30
            assert catalog.total_agents == 26
            assert _payload_bytes(catalog_result) <= 32 * 1024

            resolved_result = await session.call_tool(
                "resolve_employee_intent",
                {"intent": "My VPN is down before a customer meeting"},
            )
            resolved = EmployeeExperiencePresentation.model_validate(
                resolved_result.structuredContent
            )
            assert resolved.scenario_id == "vpn-meeting-rescue"
            assert _payload_bytes(resolved_result) <= 24 * 1024

            opened_result = await session.call_tool(
                "open_employee_experience",
                {"scenario_id": "site-health-governance"},
            )
            opened = EmployeeExperiencePresentation.model_validate(opened_result.structuredContent)
            assert opened.status.value == "blocked"
            assert any(section.kind.value == "policy_findings" for section in opened.sections)

            prepared_result = await session.call_tool(
                "prepare_employee_action",
                {
                    "request": {
                        "scenario_id": "expense-smart-submit",
                        "action_id": "ess-request-approval",
                        "form_values": {"business-purpose": "Client workshop"},
                        "confirmed": True,
                    }
                },
            )
            prepared = EmployeeActionReceipt.model_validate(prepared_result.structuredContent)
            assert prepared.state.value == "review_required"
            assert prepared.required_approver == "Expense approver"
            assert not prepared.executed
            assert _payload_bytes(prepared_result) <= 12 * 1024
            for result in (catalog_result, resolved_result, opened_result, prepared_result):
                payload = json.dumps(result.structuredContent).casefold()
                for forbidden_label in ("synthetic", "demo", "canary", "non-production"):
                    assert forbidden_label not in payload

    asyncio.run(run())


def _payload_bytes(result: CallToolResult) -> int:
    structured = result.structuredContent
    content = result.content
    structured_bytes = len(
        json.dumps(
            structured,
            separators=(",", ":"),
            ensure_ascii=True,
        ).encode("utf-8")
    )
    content_bytes = sum(len(getattr(item, "text", "").encode("utf-8")) for item in content)
    return structured_bytes + content_bytes


def test_formation_widget_is_self_contained_and_accessible() -> None:
    html = (
        Path(__file__).parents[2] / "src" / "agent_iq" / "api" / "widgets" / "formation.html"
    ).read_text(encoding="utf-8")

    assert len(html.encode("utf-8")) <= 64 * 1024
    for marker in (
        'aria-live="polite"',
        "prefers-reduced-motion",
        "ui/initialize",
        "ui/notifications/tool-input",
        "ui/notifications/tool-input-partial",
        "ui/notifications/tool-result",
        "ui/notifications/tool-cancelled",
        "ui/notifications/host-context-changed",
        "ui/notifications/size-changed",
        "ui/resource-teardown",
        "ui/request-display-mode",
        "ui/message",
        '"tools/call"',
        'role", "alert"',
        "Visible gaps",
        "textContent",
        'availableDisplayModes: ["inline", "fullscreen"]',
        'appInfo: { name: "agent-iq-formation", version: "1.0.0" }',
        'content: [{ type: "text", text }]',
        "state.hostCapabilities.serverTools",
        "state.hostCapabilities.message",
        "Evidence-backed formulation map",
        'data-answer-flow": "formulation"',
        "Selected specialist",
    ):
        assert marker in html
    for forbidden in (
        "<script src=",
        "<link ",
        "innerHTML",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "<iframe",
        "ResizeObserver",
    ):
        assert forbidden not in html


def test_formation_widget_keeps_the_demo_compact_and_uses_host_scrolling() -> None:
    html = (
        Path(__file__).parents[2] / "src" / "agent_iq" / "api" / "widgets" / "formation.html"
    ).read_text(encoding="utf-8")

    for removed in (
        "Eligible catalog scanned",
        "specialist in the formation",
        "Read-only; no approval required",
        "A2A specialist execution",
        "approved source",
        "Cited result",
        "Action risk",
        "Knowledge scope",
        "Not remembered in this canary",
        "function stageRail",
        "function trustStrip",
        "result-text",
        "overflow-y: auto",
        "app.style.maxHeight",
        'app.style.overflowY = "auto"',
    ):
        assert removed not in html
    for marker in (
        "ui/notifications/size-changed",
        "body.appendChild(formation)",
        "function renderFormulationFlow",
        "function renderAgentEvent",
        "No success is being claimed",
        "Recoverable failure",
    ):
        assert marker in html
    assert (
        "html, body {\n      margin: 0;\n      min-width: 280px;\n      overflow: hidden;"
    ) in html
    assert (
        ".shell {\n"
        "      width: min(100%, 860px);\n"
        "      min-height: 196px;\n"
        "      overflow: hidden;"
    ) in html
    assert "restoreDeadline = window.setTimeout" in html
    assert "invocation.delta" not in html
    assert "chain-of-thought" not in html.lower()


def test_employee_operations_widget_is_self_contained_accessible_and_schema_driven() -> None:
    html = (
        Path(__file__).parents[2]
        / "src"
        / "agent_iq"
        / "api"
        / "widgets"
        / "employee-operations.html"
    ).read_text(encoding="utf-8")

    assert len(html.encode("utf-8")) <= 96 * 1024
    for marker in (
        'aria-live="polite"',
        "prefers-reduced-motion",
        "ui/initialize",
        "ui/notifications/initialized",
        "ui/notifications/tool-input",
        "ui/notifications/tool-input-partial",
        "ui/notifications/tool-result",
        "ui/notifications/tool-cancelled",
        "ui/notifications/host-context-changed",
        "ui/notifications/size-changed",
        "ui/resource-teardown",
        "ui/request-display-mode",
        "ui/open-link",
        "ui/message",
        '"tools/call"',
        "open_employee_experience",
        "prepare_employee_action",
        "No enterprise action was executed",
        "No private chain-of-thought",
        "dashboard",
        "timeline",
        "form",
        "comparison_table",
        "checklist",
        "chart",
        "people_cards",
        "org_chart",
        "map",
        "gallery",
        "document_callouts",
        "decision_tree",
        "approvals",
        "policy_findings",
        "source_drawer",
        "follow_ups",
        'appInfo: { name: "agent-iq-employee-operations", version: "1.0.0" }',
        'content: [{ type: "text", text: message }]',
        'availableDisplayModes: ["inline", "fullscreen"]',
        "textContent",
    ):
        assert marker in html
    for forbidden in (
        "<script src=",
        "<link ",
        "innerHTML",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "<iframe",
        "ResizeObserver",
    ):
        assert forbidden not in html
    assert "html, body { margin: 0; min-width: 0; overflow: hidden;" in html
    assert "app.style.maxHeight" not in html
    assert "app.style.overflowY" not in html


def test_vacation_ring_widget_is_compact_accessible_and_schema_driven() -> None:
    html = (
        Path(__file__).parents[2] / "src" / "agent_iq" / "api" / "widgets" / "vacation-ring.html"
    ).read_text(encoding="utf-8")

    assert len(html.encode("utf-8")) <= 24 * 1024
    for marker in (
        'aria-live="polite"',
        "prefers-reduced-motion",
        "ui/initialize",
        "ui/notifications/initialized",
        "ui/notifications/tool-input",
        "ui/notifications/tool-input-partial",
        "ui/notifications/tool-result",
        "ui/notifications/tool-cancelled",
        "ui/notifications/host-context-changed",
        "ui/notifications/size-changed",
        "ui/resource-teardown",
        "ui/open-link",
        "compact_ring",
        "svg.dataset.vacationRings",
        "employee-vacation-leave",
        'availableDisplayModes: ["inline"]',
        'appInfo: { name: "agent-iq-vacation-ring", version: "1.1.0" }',
        "Selected by Agent IQ",
        "[1]",
        "textContent",
    ):
        assert marker in html
    for forbidden in (
        "<script src=",
        "<link ",
        "innerHTML",
        "fetch(",
        "XMLHttpRequest",
        "WebSocket",
        "<iframe",
        "ResizeObserver",
        "ui/request-display-mode",
        '"tools/call"',
        "open_employee_experience",
    ):
        assert forbidden not in html
    assert "width: 100%" in html
    assert "background: var(--surface)" in html
    assert "box-shadow: none" in html
    assert "width: min(100%, 560px)" not in html
    assert "Math.max(124" in html
    assert "getBoundingClientRect().height + 8" not in html


def test_mcp_transport_security_allows_only_local_and_public_hosts(
    service_factory: Callable[..., AgentIqService],
) -> None:
    server = create_mcp_server(
        service_factory(),
        public_base_url="https://agent-iq.example.test",
    )
    security = server.settings.transport_security
    assert security is not None
    assert not server.settings.json_response
    assert security.enable_dns_rebinding_protection
    assert security.allowed_hosts == [
        "127.0.0.1:*",
        "localhost:*",
        "[::1]:*",
        "agent-iq.example.test",
    ]
    assert security.allowed_origins[-1] == "https://agent-iq.example.test"


def test_mcp_transport_security_allows_bound_deployment_probe_host(
    service_factory: Callable[..., AgentIqService],
) -> None:
    server = create_mcp_server(
        service_factory(),
        public_base_url="https://agent-iq.example.test",
        deployment_probe_base_url="https://agent-iq--candidate-a1.example.test",
    )

    security = server.settings.transport_security
    assert security is not None
    assert security.allowed_hosts[-2:] == [
        "agent-iq.example.test",
        "agent-iq--candidate-a1.example.test",
    ]
    assert security.allowed_origins[-2:] == [
        "https://agent-iq.example.test",
        "https://agent-iq--candidate-a1.example.test",
    ]


def test_progress_update_maps_squad_lifecycle_payloads_between_selection_and_plan(
    service_factory: Callable[..., AgentIqService],
) -> None:
    service = service_factory()
    reason_hash = f"sha256:{'a' * 64}"

    proposed = _progress_update(
        SquadProposedPayload(
            request_id="request-1",
            decision_id="decision-1",
            agent_ids=("agent-alpha", "agent-beta"),
            selector="dynamic-intent-decomposition",
        ),
        service,
    )
    assert proposed is not None
    assert (proposed.key, proposed.progress, proposed.message) == (
        "squad-proposed",
        32.0,
        "Proposed the smallest justified squad",
    )

    overridden = _progress_update(
        SquadOverriddenPayload(
            request_id="request-1",
            decision_id="decision-1",
            override_id="override-1",
            proposed_agent_ids=("agent-alpha", "agent-beta"),
            authorized_agent_ids=("agent-alpha", "agent-gamma"),
            reason_code="reviewer-adjustment",
            reason_hash=reason_hash,
        ),
        service,
    )
    assert overridden is not None
    assert (overridden.key, overridden.progress, overridden.message) == (
        "squad-overridden",
        33.0,
        "Adjusted the squad composition",
    )

    authorized = _progress_update(
        SquadAuthorizedPayload(
            request_id="request-1",
            decision_id="decision-1",
            agent_ids=("agent-alpha", "agent-gamma"),
            override_id="override-1",
        ),
        service,
    )
    assert authorized is not None
    assert (authorized.key, authorized.progress, authorized.message) == (
        "squad-authorized",
        34.0,
        "Authorized the squad",
    )

    # The three squad updates must remain strictly between the existing
    # agents-selected (30%) and plan-prepared (35%) progress checkpoints.
    assert 30.0 < proposed.progress < overridden.progress < authorized.progress < 35.0
