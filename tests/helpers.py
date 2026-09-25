"""测试共用的领域搭建助手：不含真实身份信息的示例数据。"""

from __future__ import annotations

from datetime import datetime, timezone

from src.assets import (
    Material,
    MaterialKind,
    Participant,
    Project,
    Role,
    Segment,
    SegmentOrigin,
)
from src.ledger import ShareContract, StreamKind
from src.rights import License, LicenseKind, Usage
from src.service import ContentAssetService
from src.versions import Form

T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)
NOW = datetime(2026, 9, 25, tzinfo=timezone.utc)
MUSIC_EXPIRY = datetime(2026, 10, 1, tzinfo=timezone.utc)
FAR = datetime(2028, 1, 1, tzinfo=timezone.utc)

ALL_USAGES = frozenset(
    {Usage.FULL_AUDIO, Usage.VIDEO_PODCAST, Usage.PAID_ALBUM, Usage.CLIP}
)
CHANNELS = frozenset({"自有App", "播客平台", "短视频平台"})
CN = frozenset({"CN"})


def participant(participant_id: str, *roles: Role) -> Participant:
    return Participant(participant_id, f"当事人{participant_id}", frozenset(roles))


def make_license(
    license_id: str,
    kind: LicenseKind,
    licensor_id: str,
    usages: frozenset[Usage],
    *,
    subject_material_id: str | None = None,
    subject_participant_id: str | None = None,
    valid_to: datetime | None = FAR,
    channels: frozenset[str] = CHANNELS,
    territories: frozenset[str] = CN,
    signed_by: str | None = None,
) -> License:
    return License(
        license_id=license_id,
        kind=kind,
        licensor_id=licensor_id,
        usages=usages,
        channels=channels,
        territories=territories,
        valid_from=T0,
        valid_to=valid_to,
        signed_by=signed_by or licensor_id,
        signed_at=T0,
        subject_material_id=subject_material_id,
        subject_participant_id=subject_participant_id,
    )


def build_service(with_licenses: bool = True, with_contracts: bool = True) -> ContentAssetService:
    """搭建一次访谈的完整示例：参与者、素材、片段、许可与合同。"""
    service = ContentAssetService()
    people = [
        participant("creator", Role.CREATOR),
        participant("editor", Role.EDITOR),
        participant("guest", Role.RIGHTS_HOLDER),
        participant("voice", Role.RIGHTS_HOLDER),
        participant("musician", Role.RIGHTS_HOLDER),
        participant("legal", Role.LEGAL),
        participant("business", Role.BUSINESS),
        participant("ops", Role.OPS),
    ]
    for person in people:
        service.assets.add_participant(person)
    service.assets.create_project(
        Project("proj", "访谈节目", tuple(p.participant_id for p in people), T0)
    )
    service.assets.add_material(
        Material("rec", "proj", MaterialKind.RAW_RECORDING, "演播室原始录制", "creator", ("creator", "guest"), T0)
    )
    service.assets.add_material(
        Material("txt", "proj", MaterialKind.TRANSCRIPT, "访谈文字稿", "creator", ("creator", "guest"), T0)
    )
    service.assets.add_material(
        Material("music", "proj", MaterialKind.MUSIC, "片头配乐", "musician", (), T0)
    )
    service.assets.add_material(
        Material("sample", "proj", MaterialKind.VOICE_SAMPLE, "配音声音样本", "voice", (), T0)
    )
    service.assets.add_segment(
        Segment("talk", "proj", SegmentOrigin.HUMAN, ("rec", "txt", "music"), None, "txt")
    )
    service.assets.add_segment(
        Segment("intro", "proj", SegmentOrigin.SYNTHETIC, ("sample", "music"), "AI合成配音", None)
    )
    if with_licenses:
        register_standard_licenses(service)
    if with_contracts:
        for contract in (
            ShareContract("c-ad", "proj", StreamKind.AD_SHARE, "creator", 5000),
            ShareContract("c-member", "proj", StreamKind.MEMBERSHIP, "creator", 7000),
            ShareContract("c-ip", "proj", StreamKind.IP_LICENSE, "guest", 3000),
            ShareContract("c-event", "proj", StreamKind.OFFLINE_EVENT, "business", 9000),
        ):
            service.ledger.register_contract(contract)
    return service


def register_standard_licenses(service: ContentAssetService) -> None:
    """嘉宾只同意部分用途：肖像同意不含付费专辑；配乐许可有期限。"""
    service.rights.register(
        make_license("lic-guest-voice", LicenseKind.VOICE_CONSENT, "guest", ALL_USAGES, subject_participant_id="guest")
    )
    service.rights.register(
        make_license(
            "lic-guest-portrait",
            LicenseKind.PORTRAIT_CONSENT,
            "guest",
            frozenset({Usage.VIDEO_PODCAST, Usage.CLIP}),
            subject_participant_id="guest",
        )
    )
    service.rights.register(
        make_license("lic-creator-voice", LicenseKind.VOICE_CONSENT, "creator", ALL_USAGES, subject_participant_id="creator")
    )
    service.rights.register(
        make_license("lic-creator-portrait", LicenseKind.PORTRAIT_CONSENT, "creator", ALL_USAGES, subject_participant_id="creator")
    )
    service.rights.register(
        make_license(
            "lic-music",
            LicenseKind.MUSIC_LICENSE,
            "musician",
            ALL_USAGES,
            subject_material_id="music",
            valid_to=MUSIC_EXPIRY,
        )
    )
    service.rights.register(
        make_license(
            "lic-sample",
            LicenseKind.VOICE_SAMPLE_CONSENT,
            "voice",
            ALL_USAGES | {Usage.AI_VOICE},
            subject_material_id="sample",
        )
    )


def freeze(
    service: ContentAssetService,
    version_id: str,
    form: Form,
    segment_ids: tuple[str, ...] = ("talk",),
):
    return service.freeze_version(
        version_id=version_id,
        project_id="proj",
        form=form,
        segment_ids=segment_ids,
        at=NOW,
    )


def publish_plan(
    service: ContentAssetService,
    plan_id: str,
    version_id: str,
    channel: str = "自有App",
    territory: str = "CN",
    scheduled_at: datetime = NOW,
):
    service.gate.create_plan(plan_id, version_id, channel, territory, scheduled_at, NOW)
    service.gate.submit(plan_id, NOW)
    return service.gate.publish(plan_id, NOW)
