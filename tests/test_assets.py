"""资产谱系：立项、素材与片段登记的校验规则。"""

import unittest
from datetime import datetime

from src.assets import (
    Material,
    MaterialKind,
    Project,
    Role,
    Segment,
    SegmentOrigin,
)
from tests.helpers import NOW, T0, build_service, participant


class AssetRegistryTest(unittest.TestCase):
    def test_segment_requires_known_materials_in_same_project(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(KeyError, "素材不存在"):
            service.assets.add_segment(
                Segment("bad", "proj", SegmentOrigin.HUMAN, ("ghost",), None, None)
            )

    def test_synthetic_segment_must_carry_label(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "合成内容标识"):
            service.assets.add_segment(
                Segment("syn", "proj", SegmentOrigin.SYNTHETIC, ("sample",), None, None)
            )

    def test_human_segment_must_not_carry_label(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "不应携带合成内容标识"):
            service.assets.add_segment(
                Segment("hum", "proj", SegmentOrigin.HUMAN, ("rec",), "AI合成", None)
            )

    def test_duplicate_ids_are_rejected(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "参与者已存在"):
            service.assets.add_participant(participant("guest", Role.RIGHTS_HOLDER))
        with self.assertRaisesRegex(ValueError, "项目已存在"):
            service.assets.create_project(Project("proj", "重复", ("creator",), NOW))
        with self.assertRaisesRegex(ValueError, "素材已存在"):
            service.assets.add_material(
                Material("rec", "proj", MaterialKind.RAW_RECORDING, "重复", "creator", (), NOW)
            )
        with self.assertRaisesRegex(ValueError, "片段已存在"):
            service.assets.add_segment(
                Segment("talk", "proj", SegmentOrigin.HUMAN, ("rec",), None, None)
            )

    def test_material_requires_known_project_and_owner(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(KeyError, "项目不存在"):
            service.assets.add_material(
                Material("x1", "ghost", MaterialKind.EXTERNAL, "外部", "creator", (), NOW)
            )
        with self.assertRaisesRegex(KeyError, "参与者不存在"):
            service.assets.add_material(
                Material("x2", "proj", MaterialKind.EXTERNAL, "外部", "ghost", (), NOW)
            )

    def test_project_requires_participants_and_title(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "选题不能为空"):
            service.assets.create_project(Project("p2", "  ", ("creator",), NOW))
        with self.assertRaisesRegex(ValueError, "至少登记一名参与者"):
            service.assets.create_project(Project("p3", "空项目", (), NOW))

    def test_naive_time_is_rejected(self) -> None:
        service = build_service()
        with self.assertRaisesRegex(ValueError, "时区"):
            service.assets.create_project(
                Project("p4", "朴素时间", ("creator",), datetime(2026, 1, 1))
            )
        self.assertIsNotNone(T0.tzinfo)


if __name__ == "__main__":
    unittest.main()
