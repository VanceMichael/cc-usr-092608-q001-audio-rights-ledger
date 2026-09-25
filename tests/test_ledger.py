"""收入结算账本：回执幂等、冲正落账与可续做的月末关账。"""

import unittest

from src.ledger import EntryKind, Ledger, ReversalReason, ShareContract, StreamKind
from tests.helpers import NOW


def build_ledger() -> Ledger:
    ledger = Ledger()
    ledger.register_contract(ShareContract("c-ad", "proj", StreamKind.AD_SHARE, "creator", 5000))
    ledger.register_contract(ShareContract("c-member", "proj", StreamKind.MEMBERSHIP, "creator", 7000))
    return ledger


class FlakyLedger(Ledger):
    """模拟月末关账中断：写入指定数量的付款后崩溃。"""

    def __init__(self, fail_after: int) -> None:
        super().__init__()
        self._fail_after = fail_after
        self._payout_writes = 0

    def _record(self, entry) -> None:
        if entry.kind is EntryKind.PAYOUT:
            self._payout_writes += 1
            if self._payout_writes > self._fail_after:
                raise RuntimeError("月末关账中断")
        super()._record(entry)


class LedgerTest(unittest.TestCase):
    def test_receipt_ingest_is_idempotent(self) -> None:
        ledger = build_ledger()
        first = ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        replay = ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        self.assertIs(first, replay)
        self.assertEqual(len(ledger.entries("c-ad")), 1)
        with self.assertRaisesRegex(ValueError, "不一致"):
            ledger.ingest_receipt("r1", "c-ad", 9999, "2026-09", NOW)

    def test_reversal_offsets_without_touching_original(self) -> None:
        ledger = build_ledger()
        revenue = ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        ledger.post_reversal(
            "rf1", ReversalReason.REFUND, "c-ad", 3000, "2026-09", NOW, corrects=revenue.entry_id
        )
        self.assertEqual(ledger.net_revenue("c-ad"), 7000)
        # 原账目不被修改，冲正只是追加
        self.assertEqual(ledger.entries("c-ad")[0].amount, 10000)
        self.assertEqual(len(ledger.entries("c-ad")), 2)

    def test_reversal_is_capped_and_idempotent(self) -> None:
        ledger = build_ledger()
        revenue = ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        ledger.post_reversal(
            "rf1", ReversalReason.REFUND, "c-ad", 7000, "2026-09", NOW, corrects=revenue.entry_id
        )
        again = ledger.post_reversal(
            "rf1", ReversalReason.REFUND, "c-ad", 7000, "2026-09", NOW, corrects=revenue.entry_id
        )
        self.assertEqual(len(ledger.entries("c-ad")), 2)
        self.assertEqual(again.amount, -7000)
        with self.assertRaisesRegex(ValueError, "超过原收入"):
            ledger.post_reversal(
                "rf2", ReversalReason.CANCELLATION, "c-ad", 3001, "2026-09", NOW, corrects=revenue.entry_id
            )

    def test_royalty_backpay_reduces_due(self) -> None:
        ledger = build_ledger()
        ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        ledger.post_reversal("bp1", ReversalReason.ROYALTY_BACKPAY, "c-ad", 2000, "2026-09", NOW)
        self.assertEqual(ledger.net_revenue("c-ad"), 8000)
        self.assertEqual(ledger.contract_due("c-ad"), 4000)

    def test_close_period_pays_share_and_is_idempotent(self) -> None:
        ledger = build_ledger()
        ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        ledger.ingest_receipt("r2", "c-member", 20000, "2026-09", NOW)
        created = ledger.close_period("2026-09", NOW)
        self.assertEqual(len(created), 2)
        self.assertEqual(ledger.total_paid("c-ad"), 5000)
        self.assertEqual(ledger.total_paid("c-member"), 14000)
        self.assertEqual(ledger.contract_due("c-ad"), 0)
        # 再次关账不重复付款
        self.assertEqual(ledger.close_period("2026-09", NOW), [])
        self.assertEqual(len(ledger.entries()), 4)

    def test_close_period_resumes_after_crash(self) -> None:
        ledger = FlakyLedger(fail_after=1)
        ledger.register_contract(ShareContract("c-ad", "proj", StreamKind.AD_SHARE, "creator", 5000))
        ledger.register_contract(ShareContract("c-member", "proj", StreamKind.MEMBERSHIP, "creator", 7000))
        ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        ledger.ingest_receipt("r2", "c-member", 20000, "2026-09", NOW)
        with self.assertRaisesRegex(RuntimeError, "中断"):
            ledger.close_period("2026-09", NOW)
        # 中断后安全续做：只补写缺失的付款，不重复
        ledger._fail_after = 100
        created = ledger.close_period("2026-09", NOW)
        self.assertEqual(len(created), 1)
        self.assertEqual(ledger.total_paid("c-ad") + ledger.total_paid("c-member"), 19000)
        self.assertEqual(ledger.close_period("2026-09", NOW), [])

    def test_late_receipt_is_settled_by_next_close(self) -> None:
        ledger = build_ledger()
        ledger.ingest_receipt("r1", "c-ad", 10000, "2026-09", NOW)
        ledger.close_period("2026-09", NOW)
        ledger.ingest_receipt("r2", "c-ad", 4000, "2026-09", NOW)
        self.assertEqual(ledger.contract_due("c-ad"), 2000)
        created = ledger.close_period("2026-10", NOW)
        self.assertEqual(len(created), 1)
        self.assertEqual(ledger.contract_due("c-ad"), 0)

    def test_validation(self) -> None:
        ledger = build_ledger()
        with self.assertRaisesRegex(ValueError, "账期格式"):
            ledger.ingest_receipt("r1", "c-ad", 100, "2026-9", NOW)
        with self.assertRaisesRegex(ValueError, "必须为正"):
            ledger.ingest_receipt("r2", "c-ad", 0, "2026-09", NOW)
        with self.assertRaisesRegex(KeyError, "合同不存在"):
            ledger.ingest_receipt("r3", "ghost", 100, "2026-09", NOW)
        with self.assertRaisesRegex(ValueError, "分成比例"):
            ledger.register_contract(ShareContract("bad", "proj", StreamKind.AD_SHARE, "creator", 0))
        with self.assertRaisesRegex(KeyError, "被冲正的收入不存在"):
            ledger.post_reversal("rf9", ReversalReason.REFUND, "c-ad", 100, "2026-09", NOW, corrects="rcpt:ghost")


if __name__ == "__main__":
    unittest.main()
