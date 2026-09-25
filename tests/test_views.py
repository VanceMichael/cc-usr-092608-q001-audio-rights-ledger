"""角色视图：信息裁剪与权利人核对。"""

import unittest

from src.assets import Role
from src.gate import ChangeCause, PlanState
from src.versions import Form
from src.views import (
    business_brief,
    editorial_brief,
    legal_brief,
    ops_card,
    rights_holder_usage,
)
from tests.helpers import NOW, build_service, freeze, publish_plan


def build_published_service():
    service = build_service()
    freeze(service, "v-full", Form.FULL_AUDIO)
    freeze(service, "v-clip", Form.CLIP, ("intro",))
    publish_plan(service, "p1", "v-full", "自有App")
    publish_plan(service, "p2", "v-clip", "短视频平台")
    return service


class ViewTest(unittest.TestCase):
    def test_ops_card_shows_channels_unsettled_and_risks(self) -> None:
        service = build_published_service()
        service.ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        card = ops_card(service, Role.OPS, "v-full", NOW)
        channels = {entry["channel"]: entry for entry in card["channels"]}
        self.assertTrue(channels["自有App"]["available_now"])
        due_by_contract = {entry["contract_id"]: entry["due"] for entry in card["unsettled"]}
        self.assertEqual(due_by_contract["c-ad"], 5000)
        # 配乐许可 2026-10-01 到期，距离不足 30 天应提示风险
        self.assertTrue(any("即将到期" in risk for risk in card["risks"]))

    def test_ops_card_shows_takedown_reason(self) -> None:
        service = build_published_service()
        service.rights.withdraw("lic-sample", "voice", NOW, NOW)
        service.gate.impact_of_segment_change(
            "intro", ChangeCause.CONSENT_WITHDRAWN, NOW, "声音样本同意已撤回"
        )
        card = ops_card(service, Role.OPS, "v-clip", NOW)
        entry = card["channels"][0]
        self.assertEqual(entry["state"], PlanState.TAKEN_DOWN.value)
        self.assertEqual(entry["takedown_reason"], "声音样本同意已撤回")
        self.assertFalse(entry["available_now"])
        self.assertTrue(any("已撤回" in risk for risk in card["risks"]))

    def test_rights_holder_usage_survives_withdrawal(self) -> None:
        service = build_published_service()
        service.rights.withdraw("lic-sample", "voice", NOW, NOW)
        usages = rights_holder_usage(service, "voice")
        self.assertEqual(len(usages), 1)
        self.assertEqual(usages[0]["version_id"], "v-clip")
        self.assertTrue(usages[0]["withdrawn"])
        # 嘉宾的声音同意只被完整音频版本使用
        guest_usages = rights_holder_usage(service, "guest")
        self.assertEqual({usage["version_id"] for usage in guest_usages}, {"v-full"})

    def test_editorial_brief_hides_money(self) -> None:
        service = build_published_service()
        brief = editorial_brief(service, Role.EDITOR, "v-full")
        self.assertEqual(brief["form"], Form.FULL_AUDIO.value)
        self.assertTrue(brief["segments"])
        self.assertTrue(brief["scopes"])
        text = str(brief)
        for forbidden in ("contract", "due", "net_revenue", "payee", "share"):
            self.assertNotIn(forbidden, text)

    def test_business_brief_hides_segments(self) -> None:
        service = build_published_service()
        service.ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        brief = business_brief(service, Role.BUSINESS, "proj")
        contracts = {entry["contract_id"]: entry for entry in brief["contracts"]}
        self.assertEqual(contracts["c-ad"]["net_revenue"], 10000)
        self.assertEqual(contracts["c-ad"]["due"], 5000)
        self.assertNotIn("segments", brief)
        self.assertNotIn("transcript", str(brief))

    def test_legal_brief_has_terms_and_rulings_without_revenue(self) -> None:
        service = build_published_service()
        legal = service.assets.participant("legal")
        service.rights.interpret("lic-music", legal, "地域限于中国大陆", NOW)
        brief = legal_brief(service, legal, "v-full")
        by_id = {entry["license_id"]: entry for entry in brief["licenses"]}
        self.assertEqual(by_id["lic-music"]["rulings"], ["地域限于中国大陆"])
        self.assertIn("CN", by_id["lic-music"]["territories"])
        self.assertNotIn("net_revenue", str(brief))

    def test_role_enforcement(self) -> None:
        service = build_published_service()
        with self.assertRaisesRegex(PermissionError, "创作者与编辑"):
            editorial_brief(service, Role.BUSINESS, "v-full")
        with self.assertRaisesRegex(PermissionError, "平台运营"):
            ops_card(service, Role.CREATOR, "v-full", NOW)
        with self.assertRaisesRegex(PermissionError, "商务"):
            business_brief(service, Role.EDITOR, "proj")
        guest = service.assets.participant("guest")
        with self.assertRaisesRegex(PermissionError, "法务"):
            legal_brief(service, guest, "v-full")


if __name__ == "__main__":
    unittest.main()
