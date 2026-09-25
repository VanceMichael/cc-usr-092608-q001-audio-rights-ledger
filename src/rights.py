"""权利许可：同意与许可的登记、法务解释、统一撤回与覆盖判断。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .assets import AssetRegistry, MaterialKind, Participant, Role, require_aware


class LicenseKind(Enum):
    VOICE_CONSENT = "声音同意"
    PORTRAIT_CONSENT = "肖像同意"
    MUSIC_LICENSE = "音乐许可"
    MATERIAL_LICENSE = "素材许可"
    VOICE_SAMPLE_CONSENT = "声音样本同意"


class Usage(Enum):
    """许可限定的可用范围。"""

    FULL_AUDIO = "完整音频"
    VIDEO_PODCAST = "视频播客"
    PAID_ALBUM = "付费专辑"
    CLIP = "跨平台切片"
    AI_VOICE = "合成配音"


@dataclass(frozen=True)
class License:
    """一份许可或同意：限定用途、渠道、地域与期限。"""

    license_id: str
    kind: LicenseKind
    licensor_id: str
    usages: frozenset[Usage]
    channels: frozenset[str]  # 空集表示不限渠道
    territories: frozenset[str]  # 空集表示不限地域
    valid_from: datetime
    valid_to: datetime | None  # None 表示长期有效
    signed_by: str
    signed_at: datetime
    subject_material_id: str | None = None
    subject_participant_id: str | None = None


@dataclass(frozen=True)
class Withdrawal:
    """撤回事件：只追加，生效时间之后不再允许新使用。"""

    license_id: str
    requested_by: str
    effective_from: datetime
    recorded_at: datetime


@dataclass(frozen=True)
class Ruling:
    """法务对许可的解释意见。"""

    license_id: str
    interpreter_id: str
    text: str
    recorded_at: datetime


_MATERIAL_LICENSE_KINDS = {
    LicenseKind.MUSIC_LICENSE: MaterialKind.MUSIC,
    LicenseKind.MATERIAL_LICENSE: MaterialKind.EXTERNAL,
    LicenseKind.VOICE_SAMPLE_CONSENT: MaterialKind.VOICE_SAMPLE,
}
_PARTICIPANT_LICENSE_KINDS = {LicenseKind.VOICE_CONSENT, LicenseKind.PORTRAIT_CONSENT}


class RightsRegistry:
    """许可与同意的统一登记处，也是声音样本等同意的统一撤回入口。"""

    def __init__(self, assets: AssetRegistry) -> None:
        self._assets = assets
        self._licenses: dict[str, License] = {}
        self._withdrawals: list[Withdrawal] = []
        self._rulings: list[Ruling] = []

    def register(self, license_: License) -> License:
        if license_.license_id in self._licenses:
            raise ValueError("许可编号已存在")
        if not license_.usages:
            raise ValueError("许可必须限定可用范围")
        require_aware(license_.valid_from, "生效时间")
        require_aware(license_.signed_at, "签署时间")
        if license_.valid_to is not None:
            require_aware(license_.valid_to, "失效时间")
            if license_.valid_to <= license_.valid_from:
                raise ValueError("许可期限无效")
        self._assets.participant(license_.licensor_id)
        self._assets.participant(license_.signed_by)
        if license_.kind in _MATERIAL_LICENSE_KINDS:
            if license_.subject_material_id is None or license_.subject_participant_id is not None:
                raise ValueError("素材类许可必须且只能指向素材")
            material = self._assets.material(license_.subject_material_id)
            if material.kind is not _MATERIAL_LICENSE_KINDS[license_.kind]:
                raise ValueError("许可类型与素材类型不匹配")
            if material.owner_id != license_.licensor_id:
                raise ValueError("只有素材权利人可以签发该素材的许可")
        else:
            if license_.subject_participant_id is None or license_.subject_material_id is not None:
                raise ValueError("声音与肖像同意必须且只能指向当事人")
            self._assets.participant(license_.subject_participant_id)
            if license_.subject_participant_id != license_.licensor_id:
                raise ValueError("声音与肖像同意只能由当事人本人签发")
        self._licenses[license_.license_id] = license_
        return license_

    def withdraw(
        self,
        license_id: str,
        requested_by: str,
        effective_from: datetime,
        recorded_at: datetime,
    ) -> Withdrawal:
        """统一撤回入口：撤回只影响生效之后的未来使用，历史记录一律保留。"""
        license_ = self.get(license_id)
        if requested_by != license_.licensor_id:
            raise PermissionError("只能由权利人本人撤回")
        require_aware(effective_from, "撤回生效时间")
        require_aware(recorded_at, "撤回登记时间")
        event = Withdrawal(license_id, requested_by, effective_from, recorded_at)
        self._withdrawals.append(event)
        return event

    def interpret(
        self,
        license_id: str,
        interpreter: Participant,
        text: str,
        recorded_at: datetime,
    ) -> Ruling:
        """许可解释只能由法务出具，且签署该许可的人应当回避。"""
        license_ = self.get(license_id)
        if Role.LEGAL not in interpreter.roles:
            raise PermissionError("只有法务人员可以解释许可")
        if interpreter.participant_id == license_.signed_by:
            raise PermissionError("签署授权的人不能代替法务解释许可")
        if not text.strip():
            raise ValueError("解释内容不能为空")
        require_aware(recorded_at, "解释时间")
        ruling = Ruling(license_id, interpreter.participant_id, text, recorded_at)
        self._rulings.append(ruling)
        return ruling

    def covers(
        self,
        license_: License,
        usage: Usage,
        channel: str,
        territory: str,
        at: datetime,
    ) -> bool:
        """判断许可在指定时刻是否覆盖某用途、渠道与地域。"""
        if usage not in license_.usages:
            return False
        if license_.channels and channel not in license_.channels:
            return False
        if license_.territories and territory not in license_.territories:
            return False
        if at < license_.valid_from:
            return False
        if license_.valid_to is not None and at > license_.valid_to:
            return False
        return not self.is_withdrawn(license_.license_id, at)

    def is_withdrawn(self, license_id: str, at: datetime) -> bool:
        return any(
            event.license_id == license_id and event.effective_from <= at
            for event in self._withdrawals
        )

    def get(self, license_id: str) -> License:
        try:
            return self._licenses[license_id]
        except KeyError:
            raise KeyError(f"许可不存在：{license_id}") from None

    def licenses_for_material(
        self, material_id: str, kind: LicenseKind | None = None
    ) -> list[License]:
        return [
            license_
            for license_ in self._licenses.values()
            if license_.subject_material_id == material_id
            and (kind is None or license_.kind is kind)
        ]

    def licenses_for_participant(
        self, participant_id: str, kind: LicenseKind | None = None
    ) -> list[License]:
        return [
            license_
            for license_ in self._licenses.values()
            if license_.subject_participant_id == participant_id
            and (kind is None or license_.kind is kind)
        ]

    def licenses_of_licensor(self, licensor_id: str) -> list[License]:
        return [
            license_
            for license_ in self._licenses.values()
            if license_.licensor_id == licensor_id
        ]

    def withdrawals_of(self, license_id: str) -> list[Withdrawal]:
        return [event for event in self._withdrawals if event.license_id == license_id]

    def rulings_for(self, license_id: str) -> list[Ruling]:
        return [ruling for ruling in self._rulings if ruling.license_id == license_id]
