"""收入结算账本：收入按合同归集、冲正落账、回执幂等、关账可续做。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .assets import require_aware


class StreamKind(Enum):
    """四类收入流，各自按照合同归集。"""

    AD_SHARE = "广告分成"
    MEMBERSHIP = "会员收入"
    IP_LICENSE = "IP授权"
    OFFLINE_EVENT = "线下活动"


class EntryKind(Enum):
    REVENUE = "收入"
    REVERSAL = "冲正"
    PAYOUT = "分账付款"


class ReversalReason(Enum):
    REFUND = "退款"
    CANCELLATION = "撤单"
    ROYALTY_BACKPAY = "后补版权费"


@dataclass(frozen=True)
class ShareContract:
    """分成合同：一个项目的某类收入流归集到一份合同。"""

    contract_id: str
    project_id: str
    stream: StreamKind
    payee_id: str
    share_bps: int  # 收款方分成，万分比


@dataclass(frozen=True)
class LedgerEntry:
    """账本条目：金额为有符号的分，收入为正，冲正与付款为负。"""

    entry_id: str
    kind: EntryKind
    contract_id: str
    amount: int
    period: str
    reason: str
    reference: str
    recorded_at: datetime


_PERIOD = re.compile(r"\d{4}-(0[1-9]|1[0-2])")


def _check_period(period: str) -> None:
    if not _PERIOD.fullmatch(period):
        raise ValueError("账期格式应为 YYYY-MM")


class Ledger:
    """只增不改的结算账本：回执去重、冲正更正、关账幂等可续做。"""

    def __init__(self) -> None:
        self._contracts: dict[str, ShareContract] = {}
        self._entries: list[LedgerEntry] = []
        self._by_id: dict[str, LedgerEntry] = {}
        self._receipts: dict[str, str] = {}

    def register_contract(self, contract: ShareContract) -> ShareContract:
        if contract.contract_id in self._contracts:
            raise ValueError("合同已存在")
        if not 0 < contract.share_bps <= 10000:
            raise ValueError("分成比例无效")
        self._contracts[contract.contract_id] = contract
        return contract

    def ingest_receipt(
        self,
        receipt_id: str,
        contract_id: str,
        amount: int,
        period: str,
        recorded_at: datetime,
    ) -> LedgerEntry:
        """平台结算回执入账：平台可能重发回执，同一回执号只入账一次。"""
        self._contract(contract_id)
        if amount <= 0:
            raise ValueError("收入金额必须为正")
        _check_period(period)
        require_aware(recorded_at, "入账时间")
        if receipt_id in self._receipts:
            existing = self._by_id[self._receipts[receipt_id]]
            if (
                existing.contract_id != contract_id
                or existing.amount != amount
                or existing.period != period
            ):
                raise ValueError("回执与已入账记录不一致")
            return existing
        entry = LedgerEntry(
            entry_id=f"rcpt:{receipt_id}",
            kind=EntryKind.REVENUE,
            contract_id=contract_id,
            amount=amount,
            period=period,
            reason="平台结算回执",
            reference=receipt_id,
            recorded_at=recorded_at,
        )
        self._record(entry)
        self._receipts[receipt_id] = entry.entry_id
        return entry

    def post_reversal(
        self,
        reversal_id: str,
        reason: ReversalReason,
        contract_id: str,
        amount: int,
        period: str,
        recorded_at: datetime,
        corrects: str | None = None,
    ) -> LedgerEntry:
        """退款、撤单与后补版权费一律以冲正落账，原账目不修改。"""
        self._contract(contract_id)
        if amount <= 0:
            raise ValueError("冲正金额必须为正")
        _check_period(period)
        require_aware(recorded_at, "入账时间")
        entry_id = f"rvsl:{reversal_id}"
        if entry_id in self._by_id:
            existing = self._by_id[entry_id]
            if existing.amount != -amount or existing.contract_id != contract_id:
                raise ValueError("冲正单与已入账记录不一致")
            return existing
        if corrects is not None:
            target = self._by_id.get(corrects)
            if target is None or target.kind is not EntryKind.REVENUE:
                raise KeyError(f"被冲正的收入不存在：{corrects}")
            if target.contract_id != contract_id:
                raise ValueError("冲正必须落在原收入所属合同")
            already = sum(
                -entry.amount
                for entry in self._entries
                if entry.kind is EntryKind.REVERSAL and entry.reference == corrects
            )
            if already + amount > target.amount:
                raise ValueError("冲正金额超过原收入")
        entry = LedgerEntry(
            entry_id=entry_id,
            kind=EntryKind.REVERSAL,
            contract_id=contract_id,
            amount=-amount,
            period=period,
            reason=reason.value,
            reference=corrects or "",
            recorded_at=recorded_at,
        )
        self._record(entry)
        return entry

    def close_period(self, period: str, recorded_at: datetime) -> list[LedgerEntry]:
        """月末关账：按合同应付未付额分账，中断后可安全续做且不重复付款。"""
        _check_period(period)
        require_aware(recorded_at, "关账时间")
        created: list[LedgerEntry] = []
        for contract in self._contracts.values():
            payout_id = f"pay:{period}:{contract.contract_id}"
            if payout_id in self._by_id:
                continue
            due = self.contract_due(contract.contract_id)
            if due <= 0:
                continue
            entry = LedgerEntry(
                entry_id=payout_id,
                kind=EntryKind.PAYOUT,
                contract_id=contract.contract_id,
                amount=-due,
                period=period,
                reason="月末分账",
                reference=period,
                recorded_at=recorded_at,
            )
            self._record(entry)
            created.append(entry)
        return created

    def net_revenue(self, contract_id: str) -> int:
        """合同累计净收入（收入加冲正）。"""
        return sum(
            entry.amount
            for entry in self._entries
            if entry.contract_id == contract_id
            and entry.kind in (EntryKind.REVENUE, EntryKind.REVERSAL)
        )

    def total_paid(self, contract_id: str) -> int:
        """合同累计已付款。"""
        return -sum(
            entry.amount
            for entry in self._entries
            if entry.contract_id == contract_id and entry.kind is EntryKind.PAYOUT
        )

    def contract_due(self, contract_id: str) -> int:
        """合同应付未付的分账款项。"""
        contract = self._contract(contract_id)
        share = self.net_revenue(contract_id) * contract.share_bps // 10000
        return share - self.total_paid(contract_id)

    def entries(self, contract_id: str | None = None) -> list[LedgerEntry]:
        if contract_id is None:
            return list(self._entries)
        return [entry for entry in self._entries if entry.contract_id == contract_id]

    def contracts_for_project(self, project_id: str) -> list[ShareContract]:
        return [
            contract
            for contract in self._contracts.values()
            if contract.project_id == project_id
        ]

    def _contract(self, contract_id: str) -> ShareContract:
        try:
            return self._contracts[contract_id]
        except KeyError:
            raise KeyError(f"合同不存在：{contract_id}") from None

    def _record(self, entry: LedgerEntry) -> None:
        if entry.entry_id in self._by_id:
            raise ValueError(f"账目编号已存在：{entry.entry_id}")
        self._entries.append(entry)
        self._by_id[entry.entry_id] = entry
