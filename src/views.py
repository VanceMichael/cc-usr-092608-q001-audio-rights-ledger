"""角色视图：各角色只见职责内信息，权利人可核对许可去向。"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from .assets import Participant, Role
from .gate import PlanState
from .service import ContentAssetService

EXPIRING_SOON = timedelta(days=30)


def editorial_brief(
    service: ContentAssetService, role: Role, version_id: str
) -> dict[str, Any]:
    """创作者与编辑看片段、可用范围与计划状态，不看合同与金额。"""
    if role not in (Role.CREATOR, Role.EDITOR):
        raise PermissionError("该视图仅面向创作者与编辑")
    version = service.versions.get(version_id)
    segments = []
    for segment_id in version.segment_ids:
        segment = service.assets.segment(segment_id)
        segments.append(
            {
                "segment_id": segment.segment_id,
                "origin": segment.origin.value,
                "synthetic_label": segment.synthetic_label,
                "materials": [
                    service.assets.material(material_id).kind.value
                    for material_id in segment.material_ids
                ],
            }
        )
    scopes = [
        {
            "requirement": watermark.requirement.label,
            "status": watermark.status,
            "valid_to": watermark.valid_to,
        }
        for watermark in version.watermark
    ]
    plans = [
        {"channel": plan.channel, "state": plan.state.value}
        for plan in service.gate.plans_for_version(version_id)
    ]
    return {
        "version_id": version.version_id,
        "form": version.form.value,
        "segments": segments,
        "scopes": scopes,
        "plans": plans,
    }


def legal_brief(
    service: ContentAssetService, participant: Participant, version_id: str
) -> dict[str, Any]:
    """法务看许可条款、解释意见与水位，不看收入金额。"""
    if Role.LEGAL not in participant.roles:
        raise PermissionError("该视图仅面向法务人员")
    version = service.versions.get(version_id)
    licenses = []
    for watermark in version.watermark:
        if watermark.license_id is None:
            licenses.append({"requirement": watermark.requirement.label, "status": "missing"})
            continue
        license_ = service.rights.get(watermark.license_id)
        licenses.append(
            {
                "license_id": license_.license_id,
                "kind": license_.kind.value,
                "licensor_id": license_.licensor_id,
                "usages": sorted(usage.value for usage in license_.usages),
                "channels": sorted(license_.channels),
                "territories": sorted(license_.territories),
                "valid_from": license_.valid_from,
                "valid_to": license_.valid_to,
                "signed_by": license_.signed_by,
                "rulings": [
                    ruling.text for ruling in service.rights.rulings_for(license_.license_id)
                ],
                "withdrawn": service.rights.withdrawals_of(license_.license_id) != [],
            }
        )
    return {
        "version_id": version.version_id,
        "form": version.form.value,
        "frozen_at": version.frozen_at,
        "licenses": licenses,
    }


def business_brief(
    service: ContentAssetService, role: Role, project_id: str
) -> dict[str, Any]:
    """商务看合同归集与分账款项，不看片段内容。"""
    if role is not Role.BUSINESS:
        raise PermissionError("该视图仅面向商务人员")
    service.assets.project(project_id)
    contracts = [
        {
            "contract_id": contract.contract_id,
            "stream": contract.stream.value,
            "payee_id": contract.payee_id,
            "net_revenue": service.ledger.net_revenue(contract.contract_id),
            "total_paid": service.ledger.total_paid(contract.contract_id),
            "due": service.ledger.contract_due(contract.contract_id),
        }
        for contract in service.ledger.contracts_for_project(project_id)
    ]
    return {"project_id": project_id, "contracts": contracts}


def ops_card(
    service: ContentAssetService, role: Role, version_id: str, at: datetime
) -> dict[str, Any]:
    """平台运营从任一版本看到可用渠道、下架原因、未结款项与权利风险。"""
    if role is not Role.OPS:
        raise PermissionError("该视图仅面向平台运营人员")
    version = service.versions.get(version_id)
    channels = []
    for plan in service.gate.plans_for_version(version_id):
        available = plan.state is PlanState.PUBLISHED or (
            plan.state is PlanState.APPROVED and not service.gate.evaluate(plan.plan_id, at)
        )
        channels.append(
            {
                "plan_id": plan.plan_id,
                "channel": plan.channel,
                "territory": plan.territory,
                "state": plan.state.value,
                "available_now": available,
                "takedown_reason": plan.takedown_reason,
            }
        )
    unsettled = [
        {
            "contract_id": contract.contract_id,
            "stream": contract.stream.value,
            "due": service.ledger.contract_due(contract.contract_id),
        }
        for contract in service.ledger.contracts_for_project(version.project_id)
    ]
    return {
        "version_id": version.version_id,
        "form": version.form.value,
        "channels": channels,
        "unsettled": unsettled,
        "risks": _rights_risks(service, version_id, at),
    }


def rights_holder_usage(service: ContentAssetService, party_id: str) -> list[dict[str, Any]]:
    """权利人核对自己的许可曾在哪些内容中被使用，历史记录不因撤回而抹去。"""
    service.assets.participant(party_id)
    usages: list[dict[str, Any]] = []
    for license_ in service.rights.licenses_of_licensor(party_id):
        for version in service.versions.all():
            if not any(entry.license_id == license_.license_id for entry in version.watermark):
                continue
            plans = service.gate.plans_for_version(version.version_id)
            usages.append(
                {
                    "license_id": license_.license_id,
                    "kind": license_.kind.value,
                    "version_id": version.version_id,
                    "form": version.form.value,
                    "published_channels": [
                        plan.channel for plan in plans if plan.state is PlanState.PUBLISHED
                    ],
                    "withdrawn": service.rights.withdrawals_of(license_.license_id) != [],
                }
            )
    return usages


def _rights_risks(
    service: ContentAssetService, version_id: str, at: datetime
) -> list[str]:
    version = service.versions.get(version_id)
    risks: list[str] = []
    for entry in version.watermark:
        label = entry.requirement.label
        if entry.license_id is None:
            risks.append(f"授权缺失：{label}")
            continue
        license_ = service.rights.get(entry.license_id)
        if service.rights.is_withdrawn(license_.license_id, at):
            risks.append(f"同意已撤回：{label}（{license_.license_id}）")
        elif license_.valid_to is not None and license_.valid_to < at:
            risks.append(f"许可已过期：{label}（{license_.license_id}）")
        elif license_.valid_to is not None and license_.valid_to - at <= EXPIRING_SOON:
            risks.append(f"许可即将到期：{label}（{license_.license_id}，{license_.valid_to.date()}）")
        if entry.status != "active":
            risks.append(f"冻结水位异常：{label}（{entry.status}）")
    return risks
