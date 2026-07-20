"""Safe, deterministic action preparation for the employee showcase."""

from __future__ import annotations

from pathlib import Path

from .contracts import (
    READ_ONLY_ACTION_KINDS,
    EmployeeActionInput,
    EmployeeActionPreparationRequest,
    EmployeeActionReceipt,
    EmployeeActionState,
    ScenarioCategory,
)
from .loader import EmployeeDataError, resolve_employee_scenario

_APPROVER_BY_CATEGORY: dict[ScenarioCategory, str] = {
    ScenarioCategory.COMMAND_CENTER: "Employee",
    ScenarioCategory.ONBOARDING: "Hiring manager and service owner",
    ScenarioCategory.BENEFITS: "Benefits administrator",
    ScenarioCategory.PAYROLL: "Payroll specialist",
    ScenarioCategory.COMPENSATION: "Manager and HR business partner",
    ScenarioCategory.TIME_OFF: "Manager",
    ScenarioCategory.EXPENSE: "Expense approver",
    ScenarioCategory.TRAVEL: "Travel approver",
    ScenarioCategory.OFFBOARDING: "Manager, HR, and IT service owner",
    ScenarioCategory.IT_SUPPORT: "IT service owner",
    ScenarioCategory.SECURITY_ACCESS: "Resource owner and IT Security",
    ScenarioCategory.DEVICE_LIFECYCLE: "IT asset manager",
    ScenarioCategory.WORKPLACE_WELLNESS: "Employee",
    ScenarioCategory.WORKPLACE_RESERVATIONS: "Workplace services",
    ScenarioCategory.RECOGNITION: "Employee",
    ScenarioCategory.LISTENING: "People manager",
    ScenarioCategory.SALES_CUSTOMER: "Account owner",
    ScenarioCategory.FINANCE_CLOSE: "Finance control owner",
    ScenarioCategory.PROCUREMENT: "Procurement approver",
    ScenarioCategory.CONTRACTOR: "Hiring manager and access owner",
    ScenarioCategory.NEWS: "Employee",
    ScenarioCategory.ORG_DIRECTORY: "Employee",
    ScenarioCategory.VISUAL_QA: "Policy owner",
    ScenarioCategory.LEARNING: "Manager",
    ScenarioCategory.SITE_PROVISIONING: "SharePoint administrator",
    ScenarioCategory.SITE_GOVERNANCE: "Site owner and governance administrator",
}


def prepare_employee_action(
    request: EmployeeActionPreparationRequest,
    root: Path | None = None,
) -> EmployeeActionReceipt:
    """Prepare a bounded draft/review receipt without performing a write."""

    experience = resolve_employee_scenario(request.scenario_id, root)
    action = next(
        (candidate for candidate in experience.actions if candidate.action_id == request.action_id),
        None,
    )
    if action is None:
        raise EmployeeDataError(
            f"Unknown action '{request.action_id}' for scenario '{request.scenario_id}'."
        )

    read_only = action.kind in READ_ONLY_ACTION_KINDS
    if read_only:
        state = EmployeeActionState.NOT_EXECUTED
        summary = (
            f"'{action.label}' is a host-side read-only action. No enterprise operation "
            "was attempted."
        )
        approver = None
        next_steps: tuple[str, ...] = (
            "Use the corresponding view, follow-up, or export control in the UI.",
        )
    elif request.confirmed:
        state = EmployeeActionState.REVIEW_REQUIRED
        approver = _APPROVER_BY_CATEGORY[experience.category]
        summary = (
            f"The '{action.label}' draft is ready for {approver} review. It has not been "
            "submitted to a system of record."
        )
        next_steps = (
            f"Route the draft to {approver}.",
            "Wait for an authenticated system-of-record confirmation.",
            "Do not treat this receipt as proof of execution.",
        )
    else:
        state = EmployeeActionState.PREPARED
        approver = _APPROVER_BY_CATEGORY[experience.category]
        summary = (
            f"The '{action.label}' draft is prepared for review. Confirm the displayed "
            "fields before requesting approval."
        )
        next_steps = (
            "Review the prepared fields.",
            f"Confirm when ready to route the draft to {approver}.",
        )

    inputs = tuple(
        EmployeeActionInput(field_id=field_id, value=value)
        for field_id, value in sorted(request.form_values.items())
    )
    return EmployeeActionReceipt(
        receipt_id=f"{request.scenario_id}-{request.action_id}-{state.value}",
        scenario_id=request.scenario_id,
        action_id=request.action_id,
        action_kind=action.kind,
        action_class=action.action_class,
        label=action.label,
        state=state,
        summary=summary,
        requires_approval=action.requires_approval,
        required_approver=approver,
        submitted_values=inputs,
        next_steps=next_steps,
    )
