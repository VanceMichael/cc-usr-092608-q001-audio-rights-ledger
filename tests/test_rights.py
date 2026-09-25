"""权利许可：覆盖判断、统一撤回入口与法务解释回避。"""

import unittest
from datetime import datetime, timedelta, timezone

from src.assets import Role
from src.rights import LicenseKind, Usage
from tests.helpers import (
    ALL_USAGES,
    NOW,
    T0,
    build_service,
    make_license,
    participant,
)


class RightsRegistryTest(unittest.TestCase):
    def test_covers_checks_usage_channel_territory_and_term(self) -> None:
        service = build_service()
        music = service.rights.get("lic-music")
        self.assertTrue(service.rights.covers(music, Usage.CLIP, "自有App", "CN", NOW))
        self.assertFalse(service.rights.covers(music, Usage.AI_VOICE, "自有App", "CN", NOW))
        self.assertFalse(service.rights.covers(music, Usage.CLIP, "未知渠道", "CN", NOW))
        self.assertFalse(service.rights.covers(music, Usage.CLIP, "自有App", "US", NOW))
        after_expiry = datetime(2026, 10, 2, tzinfo=timezone.utc)
        self.assertFalse(service.rights.covers(music, Usage.CLIP, "自有App", "CN", after_expiry))

    def test_withdraw_only_by_licensor_and_history_is_kept(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(PermissionError, "权利人本人"):
            service.rights.withdraw("lic-sample", "editor", NOW, NOW)
        service.rights.withdraw("lic-sample", "voice", NOW, NOW)
        self.assertTrue(service.rights.is_withdrawn("lic-sample", NOW))
        day_before = NOW - timedelta(days=1)
        self.assertFalse(service.rights.is_withdrawn("lic-sample", day_before))
        # 撤回只追加事件，许可本身与历史记录仍然可查
        self.assertEqual(service.rights.get("lic-sample").licensor_id, "voice")
        self.assertEqual(len(service.rights.withdrawals_of("lic-sample")), 1)

    def test_interpret_requires_legal_role(self) -> None:
        service = build_service()
        business = service.assets.participant("business")
        with self.assertRaisesRegex(PermissionError, "法务人员"):
            service.rights.interpret("lic-music", business, "随意解释", NOW)

    def test_signer_cannot_interpret_own_license(self) -> None:
        service = build_service()
        service.assets.add_participant(participant("legal2", Role.LEGAL))
        legal = service.assets.participant("legal")
        license_ = make_license(
            "lic-signed-by-legal",
            LicenseKind.MUSIC_LICENSE,
            "musician",
            ALL_USAGES,
            subject_material_id="music",
            signed_by="legal",
        )
        service.rights.register(license_)
        with self.assertRaisesRegex(PermissionError, "签署授权的人不能代替法务"):
            service.rights.interpret("lic-signed-by-legal", legal, "自己签的自己解释", NOW)
        other_legal = service.assets.participant("legal2")
        ruling = service.rights.interpret("lic-signed-by-legal", other_legal, "地域限于中国大陆", NOW)
        self.assertEqual(ruling.interpreter_id, "legal2")
        self.assertEqual(len(service.rights.rulings_for("lic-signed-by-legal")), 1)

    def test_register_validates_subject_and_licensor(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "只能指向素材"):
            service.rights.register(
                make_license("x1", LicenseKind.MUSIC_LICENSE, "musician", ALL_USAGES, subject_participant_id="guest")
            )
        with self.assertRaisesRegex(ValueError, "类型不匹配"):
            service.rights.register(
                make_license("x2", LicenseKind.MUSIC_LICENSE, "voice", ALL_USAGES, subject_material_id="sample")
            )
        with self.assertRaisesRegex(ValueError, "素材权利人"):
            service.rights.register(
                make_license("x3", LicenseKind.MUSIC_LICENSE, "guest", ALL_USAGES, subject_material_id="music")
            )
        with self.assertRaisesRegex(ValueError, "当事人本人"):
            service.rights.register(
                make_license("x4", LicenseKind.VOICE_CONSENT, "creator", ALL_USAGES, subject_participant_id="guest")
            )
        with self.assertRaisesRegex(ValueError, "可用范围"):
            service.rights.register(
                make_license("x5", LicenseKind.VOICE_CONSENT, "guest", frozenset(), subject_participant_id="guest")
            )

    def test_license_term_must_be_valid(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "期限无效"):
            service.rights.register(
                make_license(
                    "x6",
                    LicenseKind.VOICE_CONSENT,
                    "guest",
                    ALL_USAGES,
                    subject_participant_id="guest",
                    valid_to=T0,
                )
            )


if __name__ == "__main__":
    unittest.main()
