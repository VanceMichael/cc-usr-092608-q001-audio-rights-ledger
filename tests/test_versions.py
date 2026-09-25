"""成品版本：权利水位冻结与不可变性。"""

import dataclasses
import unittest

from src.rights import LicenseKind, Usage
from src.versions import Form
from tests.helpers import NOW, build_service, freeze, make_license


class VersionFreezeTest(unittest.TestCase):
    def test_freeze_captures_requirements_and_active_watermark(self) -> None:
        service = build_service()
        version = freeze(service, "v-video", Form.VIDEO_PODCAST)
        kinds = {requirement.kind for requirement in version.requirements}
        # 视频播客需要声音与肖像双重同意，并覆盖音乐许可
        self.assertIn(LicenseKind.VOICE_CONSENT, kinds)
        self.assertIn(LicenseKind.PORTRAIT_CONSENT, kinds)
        self.assertIn(LicenseKind.MUSIC_LICENSE, kinds)
        self.assertTrue(version.watermark)
        self.assertTrue(all(entry.status == "active" for entry in version.watermark))

    def test_synthetic_segment_requires_voice_sample_consent(self) -> None:
        service = build_service()
        version = freeze(service, "v-clip", Form.CLIP, ("intro",))
        sample_reqs = [
            requirement
            for requirement in version.requirements
            if requirement.kind is LicenseKind.VOICE_SAMPLE_CONSENT
        ]
        usages = {requirement.usage for requirement in sample_reqs}
        self.assertIn(Usage.AI_VOICE, usages)
        self.assertIn(Usage.CLIP, usages)

    def test_missing_consent_is_recorded_in_watermark(self) -> None:
        service = build_service(with_licenses=False)
        service.rights.register(
            make_license(
                "lic-guest-voice",
                LicenseKind.VOICE_CONSENT,
                "guest",
                frozenset({Usage.FULL_AUDIO}),
                subject_participant_id="guest",
            )
        )
        version = freeze(service, "v-full", Form.FULL_AUDIO)
        status_of = {
            (entry.requirement.kind, entry.requirement.participant_id): entry.status
            for entry in version.watermark
        }
        self.assertEqual(
            status_of[(LicenseKind.VOICE_CONSENT, "guest")], "active"
        )
        self.assertEqual(
            status_of[(LicenseKind.VOICE_CONSENT, "creator")], "missing"
        )
        music_entries = [
            entry
            for entry in version.watermark
            if entry.requirement.kind is LicenseKind.MUSIC_LICENSE
        ]
        self.assertEqual(music_entries[0].status, "missing")

    def test_limited_license_is_recorded_as_limited(self) -> None:
        service = build_service(with_licenses=False)
        service.rights.register(
            make_license(
                "lic-music-narrow",
                LicenseKind.MUSIC_LICENSE,
                "musician",
                frozenset({Usage.FULL_AUDIO}),
                subject_material_id="music",
            )
        )
        version = freeze(service, "v-clip", Form.CLIP, ("intro",))
        music_entries = [
            entry
            for entry in version.watermark
            if entry.requirement.kind is LicenseKind.MUSIC_LICENSE
        ]
        self.assertEqual(music_entries[0].status, "limited")

    def test_frozen_version_is_immutable(self) -> None:
        service = build_service()
        version = freeze(service, "v-full", Form.FULL_AUDIO)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            version.segment_ids = ()

    def test_duplicate_version_and_unknown_segment_are_rejected(self) -> None:
        service = build_service()
        freeze(service, "v-full", Form.FULL_AUDIO)
        with self.assertRaisesRegex(ValueError, "版本已存在"):
            freeze(service, "v-full", Form.CLIP)
        with self.assertRaisesRegex(KeyError, "片段不存在"):
            freeze(service, "v-ghost", Form.CLIP, ("ghost",))
        with self.assertRaisesRegex(ValueError, "至少引用一个片段"):
            freeze(service, "v-empty", Form.CLIP, ())


if __name__ == "__main__":
    unittest.main()
