"""发布门禁：先审后发、过期停在补审、片段变更的影响分析。"""

import unittest
from datetime import datetime, timedelta, timezone

from src.gate import ChangeCause, GateIssueKind, ImpactAction, PlanState
from src.rights import LicenseKind, Usage
from src.versions import Form
from tests.helpers import (
    ALL_USAGES,
    MUSIC_EXPIRY,
    NOW,
    build_service,
    freeze,
    make_license,
    publish_plan,
)

AFTER_EXPIRY = datetime(2026, 10, 5, tzinfo=timezone.utc)


class GateTest(unittest.TestCase):
    def test_submit_approve_and_publish(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        plan = publish_plan(service, "p1", "v-full")
        self.assertIs(plan.state, PlanState.PUBLISHED)
        self.assertEqual(plan.published_at, NOW)
        states = [state for _, state, _ in plan.history]
        self.assertEqual(
            states, [PlanState.DRAFT, PlanState.APPROVED, PlanState.PUBLISHED]
        )

    def test_license_expiring_before_deadline_halts_at_re_review(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        # 配乐许可 2026-10-01 到期，发布截止排在 2026-10-05
        service.gate.create_plan("p1", "v-full", "自有App", "CN", AFTER_EXPIRY, NOW)
        issues = service.gate.submit("p1", NOW)
        self.assertEqual(service.gate.get("p1").state, PlanState.RE_REVIEW)
        self.assertIn(GateIssueKind.LICENSE_EXPIRED, {issue.kind for issue in issues})
        with self.assertRaisesRegex(RuntimeError, "不能先上线再补手续"):
            service.gate.publish("p1", NOW)

    def test_publish_requires_approval(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        service.gate.create_plan("p1", "v-full", "自有App", "CN", NOW, NOW)
        with self.assertRaisesRegex(RuntimeError, "不能先上线再补手续"):
            service.gate.publish("p1", NOW)

    def test_withdrawn_voice_sample_consent_blocks_publish(self) -> None:
        service = build_service()
        freeze(service, "v-clip", Form.CLIP, ("intro",))
        service.rights.withdraw("lic-sample", "voice", NOW, NOW)
        service.gate.create_plan("p1", "v-clip", "短视频平台", "CN", NOW, NOW)
        issues = service.gate.submit("p1", NOW)
        self.assertEqual(service.gate.get("p1").state, PlanState.RE_REVIEW)
        self.assertIn(GateIssueKind.CONSENT_WITHDRAWN, {issue.kind for issue in issues})

    def test_scope_gap_blocks_uncovered_channel(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        service.gate.create_plan("p1", "v-full", "未授权渠道", "CN", NOW, NOW)
        issues = service.gate.submit("p1", NOW)
        self.assertIn(GateIssueKind.SCOPE_GAP, {issue.kind for issue in issues})
        self.assertEqual(service.gate.get("p1").state, PlanState.RE_REVIEW)

    def test_missing_portrait_consent_blocks_video_podcast(self) -> None:
        service = build_service()
        service.rights.withdraw("lic-guest-portrait", "guest", NOW, NOW)
        freeze(service, "v-video", Form.VIDEO_PODCAST)
        service.gate.create_plan("p1", "v-video", "自有App", "CN", NOW, NOW)
        issues = service.gate.submit("p1", NOW)
        self.assertIn(GateIssueKind.CONSENT_WITHDRAWN, {issue.kind for issue in issues})

    def test_renewed_license_unblocks_plan(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        service.gate.create_plan("p1", "v-full", "自有App", "CN", AFTER_EXPIRY, NOW)
        service.gate.submit("p1", NOW)
        self.assertEqual(service.gate.get("p1").state, PlanState.RE_REVIEW)
        # 补授权后重新送审，通过才能发布
        service.rights.register(
            make_license(
                "lic-music-2",
                LicenseKind.MUSIC_LICENSE,
                "musician",
                ALL_USAGES,
                subject_material_id="music",
            )
        )
        self.assertEqual(service.gate.submit("p1", MUSIC_EXPIRY), [])
        plan = service.gate.publish("p1", MUSIC_EXPIRY)
        self.assertIs(plan.state, PlanState.PUBLISHED)

    def test_impact_of_content_edit_lists_published_forms(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        freeze(service, "v-video", Form.VIDEO_PODCAST)
        freeze(service, "v-clip", Form.CLIP, ("intro",))
        publish_plan(service, "p1", "v-full", "自有App")
        publish_plan(service, "p2", "v-full", "播客平台")
        publish_plan(service, "p3", "v-clip", "短视频平台")
        impacts = service.gate.impact_of_segment_change("talk", ChangeCause.CONTENT_EDIT, NOW)
        by_plan = {impact.plan_id: impact for impact in impacts if impact.plan_id}
        # talk 只被完整音频与视频播客引用，切片版本不受影响
        self.assertEqual(set(by_plan), {"p1", "p2"})
        self.assertTrue(
            all(impact.action is ImpactAction.RE_REVIEW for impact in by_plan.values())
        )
        self.assertEqual(service.gate.get("p1").state, PlanState.RE_REVIEW)
        self.assertEqual(service.gate.get("p3").state, PlanState.PUBLISHED)
        # 视频播客没有在途计划，也要列出待重审
        version_impacts = {impact.version_id for impact in impacts}
        self.assertIn("v-video", version_impacts)

    def test_impact_of_withdrawal_takes_down_and_keeps_records(self) -> None:
        service = build_service()
        freeze(service, "v-clip", Form.CLIP, ("intro",))
        publish_plan(service, "p1", "v-clip", "短视频平台")
        service.rights.withdraw("lic-sample", "voice", NOW, NOW)
        impacts = service.gate.impact_of_segment_change(
            "intro", ChangeCause.CONSENT_WITHDRAWN, NOW, "声音样本同意已撤回"
        )
        self.assertEqual(impacts[0].action, ImpactAction.TAKEDOWN)
        plan = service.gate.get("p1")
        self.assertIs(plan.state, PlanState.TAKEN_DOWN)
        self.assertEqual(plan.takedown_reason, "声音样本同意已撤回")
        # 撤回不抹去此前合法发布的责任记录
        self.assertEqual(plan.published_at, NOW)
        states = [state for _, state, _ in plan.history]
        self.assertIn(PlanState.PUBLISHED, states)
        self.assertIn(PlanState.TAKEN_DOWN, states)

    def test_impact_of_expired_license_marks_re_license(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        publish_plan(service, "p1", "v-full", "自有App")
        impacts = service.gate.impact_of_segment_change(
            "talk", ChangeCause.LICENSE_EXPIRED, NOW, "配乐许可到期"
        )
        self.assertEqual(impacts[0].action, ImpactAction.RE_LICENSE)
        self.assertEqual(impacts[0].reason, "配乐许可到期")

    def test_impact_demotes_approved_plan_back_to_review(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        service.gate.create_plan("p1", "v-full", "自有App", "CN", NOW, NOW)
        service.gate.submit("p1", NOW)
        self.assertEqual(service.gate.get("p1").state, PlanState.APPROVED)
        service.gate.impact_of_segment_change("talk", ChangeCause.CONTENT_EDIT, NOW)
        self.assertEqual(service.gate.get("p1").state, PlanState.RE_REVIEW)


if __name__ == "__main__":
    unittest.main()
