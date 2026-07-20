"""Build, preview, and rehearse the Agent IQ employee-operations showcase."""

from __future__ import annotations

import argparse
import json
import shutil
import webbrowser
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from agent_iq.employee import (
    EmployeeActionPreparationRequest,
    EmployeeExperiencePresentation,
    EmployeeScenarioRequest,
    list_employee_scenarios,
    prepare_employee_action,
    resolve_employee_request,
    resolve_employee_scenario,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREVIEW_ROOT = ROOT / "dist" / "employee-showcase"
PREVIEW_TEMPLATE = ROOT / "apps" / "employee-copilot" / "preview" / "index.html"
WIDGET_SOURCE = ROOT / "src" / "agent_iq" / "api" / "widgets" / "employee-operations.html"

DEMO_PROMPTS = (
    "Show me my day: urgent tasks, relevant company news, and approvals at risk.",
    "Create a parental leave plan across HR, Benefits, Payroll, IT, and my manager.",
    "My VPN is down and my customer meeting starts in ten minutes.",
    "My flight was cancelled and I need to expense a hotel tonight.",
    "Explain why my access to the finance site is blocked and show the approved remediation.",
)


def export_preview(output_root: Path = DEFAULT_PREVIEW_ROOT) -> Path:
    """Export a strict-host browser preview from the authoritative Python models."""

    output_root.mkdir(parents=True, exist_ok=True)
    catalog = list_employee_scenarios()
    experiences = {
        card.scenario_id: resolve_employee_scenario(card.scenario_id).model_dump(
            mode="json", by_alias=True
        )
        for card in catalog.scenarios
    }
    (output_root / "catalog.json").write_text(
        json.dumps(catalog.model_dump(mode="json", by_alias=True), indent=2) + "\n",
        encoding="utf-8",
    )
    (output_root / "experiences.json").write_text(
        json.dumps(experiences, indent=2) + "\n",
        encoding="utf-8",
    )
    shutil.copyfile(PREVIEW_TEMPLATE, output_root / "index.html")
    shutil.copyfile(WIDGET_SOURCE, output_root / "employee-operations.html")
    return output_root / "index.html"


def _print_experience(experience: EmployeeExperiencePresentation) -> None:
    print(f"\n{experience.title} [{experience.status_label}]")
    print(experience.subtitle)
    print("Squad: " + ", ".join(agent.display_name for agent in experience.selected_agents))
    print("Observable stages:")
    for phase in experience.phases:
        print(f"  - {phase.status_label}: {phase.title} -> {phase.output_label}")
    print("Rich UX: " + ", ".join(section.kind.value for section in experience.sections))
    print("Safe actions:")
    for action in experience.actions:
        print(
            f"  - {action.label} [{action.action_class.value}; approval={action.requires_approval}]"
        )
    print(f"Sources: {len(experience.sources)} | executed actions: 0")


def _print_catalog() -> None:
    catalog = list_employee_scenarios()
    print(
        f"Agent IQ Employee Operations: {len(catalog.scenarios)} scenarios, "
        f"{catalog.total_agents} agents, {catalog.total_knowledge_sources} knowledge sources\n"
    )
    for card in catalog.scenarios:
        print(f"{card.scenario_id:30} {card.category.value:24} {card.title}")


def _print_rehearsal() -> None:
    print("Agent IQ Employee Operations - five-minute rehearsal")
    for index, prompt in enumerate(DEMO_PROMPTS, start=1):
        experience = resolve_employee_request(EmployeeScenarioRequest(intent=prompt))
        assert isinstance(experience, EmployeeExperiencePresentation)
        print(f"\nACT {index}: {prompt}")
        _print_experience(experience)


def _parse_fields(raw_fields: list[str]) -> dict[str, str]:
    fields: dict[str, str] = {}
    for raw in raw_fields:
        field_id, separator, value = raw.partition("=")
        if not separator or not field_id.strip() or not value.strip():
            raise ValueError(f"Action field must use non-empty key=value syntax: {raw!r}")
        fields[field_id.strip()] = value.strip()
    return fields


def _dump_json(value: Any) -> None:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json", by_alias=True)
    print(json.dumps(value, indent=2))


def _serve_preview(output_root: Path, host: str, port: int, open_browser: bool) -> None:
    handler = partial(SimpleHTTPRequestHandler, directory=str(output_root))
    server = ThreadingHTTPServer((host, port), handler)
    url = f"http://{host}:{port}/"
    print(f"Employee Operations preview: {url}")
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nPreview stopped.")
    finally:
        server.server_close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "mode",
        choices=("catalog", "scenario", "intent", "action", "rehearse", "preview"),
    )
    parser.add_argument("--scenario")
    parser.add_argument("--intent")
    parser.add_argument("--action-id")
    parser.add_argument("--field", action="append", default=[])
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_PREVIEW_ROOT)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--export-only", action="store_true")
    parser.add_argument("--no-browser", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    arguments = parser.parse_args()
    if arguments.mode == "catalog":
        _print_catalog()
        return 0
    if arguments.mode == "rehearse":
        _print_rehearsal()
        return 0
    if arguments.mode == "preview":
        index_path = export_preview(arguments.output)
        print(f"Preview exported to {index_path}")
        if not arguments.export_only:
            _serve_preview(
                arguments.output.resolve(),
                arguments.host,
                arguments.port,
                not arguments.no_browser,
            )
        return 0
    if arguments.mode == "scenario":
        if not arguments.scenario:
            parser.error("--scenario is required for scenario mode")
        experience = resolve_employee_scenario(arguments.scenario)
        _dump_json(experience) if arguments.json else _print_experience(experience)
        return 0
    if arguments.mode == "intent":
        if not arguments.intent:
            parser.error("--intent is required for intent mode")
        resolved = resolve_employee_request(EmployeeScenarioRequest(intent=arguments.intent))
        assert isinstance(resolved, EmployeeExperiencePresentation)
        _dump_json(resolved) if arguments.json else _print_experience(resolved)
        return 0
    if not arguments.scenario or not arguments.action_id:
        parser.error("--scenario and --action-id are required for action mode")
    receipt = prepare_employee_action(
        EmployeeActionPreparationRequest(
            scenario_id=arguments.scenario,
            action_id=arguments.action_id,
            form_values=_parse_fields(arguments.field),
            confirmed=arguments.confirm,
        )
    )
    _dump_json(receipt)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
