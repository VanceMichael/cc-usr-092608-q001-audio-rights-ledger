"""多形态发布门禁：先审后发，授权失效的计划必须停在补审阶段。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from .assets import AssetRegistry, SegmentOrigin, require_aware
from .rights import License, RightsRegistry
from .versions import Requirement, VersionStore


class PlanState(Enum):
    DRAFT = "草稿"
    RE_REVIEW = "补审"
    APPROVED = "审核通过"
    PUBLISHED = "已发布"
    TAKEN_DOWN = "已下架"


class GateIssueKind(Enum):
    MISSING_CONSENT = "授权缺失"
    LICENSE_EXPIRED = "许可过期"
    SCOPE_GAP = "范围不符"
    CONSENT_WITHDRAWN = "同意已撤回"
    SYNTHETIC_UNLABELED = "合成内容未标识"


class ChangeCause(Enum):
    CONTENT_EDIT = "内容修改"
    LICENSE_EXPIRED = "许可过期"
    SCOPE_REDUCED = "范围收缩"
    CONSENT_WITHDRAWN = "同意撤回"


class ImpactAction(Enum):
    RE_REVIEW = "重审"
    RE_LICENSE = "补授权"
    TAKEDOWN = "下架"


@dataclass(frozen=True)
class GateIssue:
    kind: GateIssueKind
    license_id: str | None
    requirement: str
    detail: str


@dataclass(frozen=True)
class Impact:
    """片段变更对某个已发布或在途形态的影响。"""

    version_id: str
    plan_id: str | None
    form: str
    channel: str | None
    action: ImpactAction
    reason: str


@dataclass
class DistributionPlan:
    """分发计划：状态轨迹全程留痕，下架不删除历史记录。"""

    plan_id: str
    version_id: str
    channel: str
    territory: str
    scheduled_at: datetime
    state: PlanState = PlanState.DRAFT
    published_at: datetime | None = None
    takedown_reason: str | None = None
    history: list[tuple[datetime, PlanState, str]] = field(default_factory=list)


_PUBLISHED_ACTION = {
    ChangeCause.CONTENT_EDIT: ImpactAction.RE_REVIEW,
    ChangeCause.LICENSE_EXPIRED: ImpactAction.RE_LICENSE,
    ChangeCause.SCOPE_REDUCED: ImpactAction.RE_LICENSE,
    ChangeCause.CONSENT_WITHDRAWN: ImpactAction.TAKEDOWN,
}


class DistributionGate:
    """发布门禁：评估权利覆盖，未通过的计划停在补审，不能先上线再补手续。"""

    def __init__(
        self,
        assets: AssetRegistry,
        rights: RightsRegistry,
        versions: VersionStore,
    ) -> None:
        self._assets = assets
        self._rights = rights
        self._versions = versions
        self._plans: dict[str, DistributionPlan] = {}

    def create_plan(
        self,
        plan_id: str,
        version_id: str,
        channel: str,
        territory: str,
        scheduled_at: datetime,
        at: datetime,
    ) -> DistributionPlan:
        if plan_id in self._plans:
            raise ValueError("分发计划已存在")
        self._versions.get(version_id)
        require_aware(scheduled_at, "发布截止时间")
        require_aware(at, "创建时间")
        plan = DistributionPlan(plan_id, version_id, channel, territory, scheduled_at)
        plan.history.append((at, PlanState.DRAFT, "创建计划"))
        self._plans[plan_id] = plan
        return plan

    def evaluate(self, plan_id: str, at: datetime) -> list[GateIssue]:
        """评估计划在指定时刻是否满足全部权利要求。"""
        plan = self.get(plan_id)
        version = self._versions.get(plan.version_id)
        issues: list[GateIssue] = []
        for segment_id in version.segment_ids:
            segment = self._assets.segment(segment_id)
            if segment.origin is SegmentOrigin.SYNTHETIC and not (segment.synthetic_label or "").strip():
                issues.append(GateIssue(GateIssueKind.SYNTHETIC_UNLABELED, None, segment_id, "合成片段缺少合成内容标识"))
        for requirement in version.requirements:
            issue = self._check_requirement(requirement, plan, at)
            if issue is not None:
                issues.append(issue)
        return issues

    def submit(self, plan_id: str, at: datetime) -> list[GateIssue]:
        """送审：有问题停在补审阶段，全部通过才进入审核通过。"""
        require_aware(at, "送审时间")
        plan = self.get(plan_id)
        if plan.state is PlanState.PUBLISHED:
            raise RuntimeError("已发布的计划不能重新送审")
        issues = self.evaluate(plan_id, plan.scheduled_at)
        if issues:
            detail = "；".join(issue.detail for issue in issues)
            self._transition(plan, PlanState.RE_REVIEW, at, f"补审：{detail}")
        else:
            self._transition(plan, PlanState.APPROVED, at, "审核通过")
        return issues

    def publish(self, plan_id: str, at: datetime) -> DistributionPlan:
        """发布前复核：授权在发布前失效的计划退回补审，不能先上线再补手续。"""
        require_aware(at, "发布时间")
        plan = self.get(plan_id)
        if plan.state is not PlanState.APPROVED:
            raise RuntimeError("分发计划未通过审核，不能先上线再补手续")
        issues = self.evaluate(plan_id, at)
        if issues:
            self._transition(plan, PlanState.RE_REVIEW, at, "发布前复核未通过，退回补审")
            raise RuntimeError("授权在发布前失效，计划退回补审")
        plan.published_at = at
        self._transition(plan, PlanState.PUBLISHED, at, "已发布")
        return plan

    def takedown(self, plan_id: str, reason: str, at: datetime) -> DistributionPlan:
        """下架：保留发布与下架的责任记录，不删除任何历史。"""
        require_aware(at, "下架时间")
        plan = self.get(plan_id)
        if plan.state is not PlanState.PUBLISHED:
            raise RuntimeError("只有已发布的计划可以下架")
        plan.takedown_reason = reason
        self._transition(plan, PlanState.TAKEN_DOWN, at, f"下架：{reason}")
        return plan

    def impact_of_segment_change(
        self,
        segment_id: str,
        cause: ChangeCause,
        at: datetime,
        reason: str = "",
    ) -> list[Impact]:
        """片段变更时，列出需要重审、下架或补授权的全部已发布形态。"""
        require_aware(at, "变更时间")
        self._assets.segment(segment_id)
        impacts: list[Impact] = []
        for version in self._versions.versions_with_segment(segment_id):
            plans = [plan for plan in self._plans.values() if plan.version_id == version.version_id]
            if not plans:
                impacts.append(Impact(version.version_id, None, version.form.value, None, ImpactAction.RE_REVIEW, reason or "片段变更，版本待重审"))
            for plan in plans:
                if plan.state is PlanState.TAKEN_DOWN:
                    continue
                if plan.state is PlanState.PUBLISHED:
                    action = _PUBLISHED_ACTION[cause]
                    if action is ImpactAction.TAKEDOWN:
                        self.takedown(plan.plan_id, reason or "关联同意已撤回", at)
                    elif action is ImpactAction.RE_REVIEW:
                        self._transition(plan, PlanState.RE_REVIEW, at, reason or "片段变更，已发布形态退回重审")
                    impacts.append(Impact(version.version_id, plan.plan_id, version.form.value, plan.channel, action, reason or cause.value))
                else:
                    self._transition(plan, PlanState.RE_REVIEW, at, reason or "片段变更，退回补审")
                    impacts.append(Impact(version.version_id, plan.plan_id, version.form.value, plan.channel, ImpactAction.RE_REVIEW, reason or cause.value))
        return impacts

    def get(self, plan_id: str) -> DistributionPlan:
        try:
            return self._plans[plan_id]
        except KeyError:
            raise KeyError(f"分发计划不存在：{plan_id}") from None

    def plans_for_version(self, version_id: str) -> list[DistributionPlan]:
        return [plan for plan in self._plans.values() if plan.version_id == version_id]

    def _check_requirement(
        self, requirement: Requirement, plan: DistributionPlan, at: datetime
    ) -> GateIssue | None:
        if requirement.material_id is not None:
            candidates = self._rights.licenses_for_material(requirement.material_id, requirement.kind)
        else:
            candidates = self._rights.licenses_for_participant(requirement.participant_id, requirement.kind)
        if not candidates:
            return GateIssue(GateIssueKind.MISSING_CONSENT, None, requirement.label, f"{requirement.label}缺失")
        live = [c for c in candidates if not self._rights.is_withdrawn(c.license_id, at)]
        if not live:
            return GateIssue(GateIssueKind.CONSENT_WITHDRAWN, candidates[0].license_id, requirement.label, f"{requirement.label}已撤回")
        in_term = [
            c for c in live
            if c.valid_from <= at and (c.valid_to is None or at <= c.valid_to)
        ]
        if not in_term:
            return GateIssue(GateIssueKind.LICENSE_EXPIRED, live[0].license_id, requirement.label, f"{requirement.label}在发布截止前已过期")
        covering = [
            c for c in in_term
            if self._rights.covers(c, requirement.usage, plan.channel, plan.territory, at)
        ]
        if not covering:
            return GateIssue(GateIssueKind.SCOPE_GAP, in_term[0].license_id, requirement.label, f"{requirement.label}不覆盖该用途、渠道或地域")
        return None

    @staticmethod
    def _transition(plan: DistributionPlan, state: PlanState, at: datetime, note: str) -> None:
        plan.state = state
        plan.history.append((at, state, note))
