"""成品版本：冻结所引用素材与权利水位，冻结结果不可更改。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum

from .assets import AssetRegistry, MaterialKind, require_aware
from .rights import LicenseKind, RightsRegistry, Usage


class Form(Enum):
    """一次录制的多形态成品。"""

    FULL_AUDIO = "完整音频"
    VIDEO_PODCAST = "视频播客"
    PAID_ALBUM = "付费专辑"
    CLIP = "跨平台切片"


FORM_USAGE = {
    Form.FULL_AUDIO: Usage.FULL_AUDIO,
    Form.VIDEO_PODCAST: Usage.VIDEO_PODCAST,
    Form.PAID_ALBUM: Usage.PAID_ALBUM,
    Form.CLIP: Usage.CLIP,
}


@dataclass(frozen=True)
class Requirement:
    """版本发布所需的一项权利，由片段引用的素材推导得出。"""

    kind: LicenseKind
    usage: Usage
    material_id: str | None
    participant_id: str | None
    label: str


@dataclass(frozen=True)
class WatermarkEntry:
    """单项权利需求在冻结时刻的水位：active / limited / missing。"""

    requirement: Requirement
    license_id: str | None
    status: str
    valid_to: datetime | None


@dataclass(frozen=True)
class EpisodeVersion:
    """冻结后的成品版本：引用素材与权利水位一并固定。"""

    version_id: str
    project_id: str
    form: Form
    segment_ids: tuple[str, ...]
    requirements: tuple[Requirement, ...]
    watermark: tuple[WatermarkEntry, ...]
    frozen_at: datetime


def build_requirements(
    assets: AssetRegistry,
    project_id: str,
    form: Form,
    segment_ids: tuple[str, ...],
) -> tuple[Requirement, ...]:
    """按形态与片段推导发布所需的全部权利。"""
    usage = FORM_USAGE[form]
    requirements: list[Requirement] = []
    seen: set[tuple[object, ...]] = set()

    def add(requirement: Requirement) -> None:
        key = (
            requirement.kind,
            requirement.usage,
            requirement.material_id,
            requirement.participant_id,
        )
        if key not in seen:
            seen.add(key)
            requirements.append(requirement)

    for segment_id in segment_ids:
        segment = assets.segment(segment_id)
        if segment.project_id != project_id:
            raise ValueError("版本引用的片段不属于本项目")
        for material_id in segment.material_ids:
            material = assets.material(material_id)
            if material.kind is MaterialKind.MUSIC:
                add(Requirement(LicenseKind.MUSIC_LICENSE, usage, material_id, None, f"音乐{material_id}的{usage.value}许可"))
            elif material.kind is MaterialKind.EXTERNAL:
                add(Requirement(LicenseKind.MATERIAL_LICENSE, usage, material_id, None, f"外部素材{material_id}的{usage.value}许可"))
            elif material.kind is MaterialKind.VOICE_SAMPLE:
                add(Requirement(LicenseKind.VOICE_SAMPLE_CONSENT, Usage.AI_VOICE, material_id, None, f"声音样本{material_id}的合成配音同意"))
                add(Requirement(LicenseKind.VOICE_SAMPLE_CONSENT, usage, material_id, None, f"声音样本{material_id}的{usage.value}同意"))
            if material.kind in (MaterialKind.RAW_RECORDING, MaterialKind.TRANSCRIPT):
                for participant_id in material.participant_ids:
                    add(Requirement(LicenseKind.VOICE_CONSENT, usage, None, participant_id, f"当事人{participant_id}的声音同意"))
                    if form is Form.VIDEO_PODCAST:
                        add(Requirement(LicenseKind.PORTRAIT_CONSENT, usage, None, participant_id, f"当事人{participant_id}的肖像同意"))
    return tuple(requirements)


def _watermark_entry(
    rights: RightsRegistry, requirement: Requirement, at: datetime
) -> WatermarkEntry:
    if requirement.material_id is not None:
        candidates = rights.licenses_for_material(requirement.material_id, requirement.kind)
    else:
        candidates = rights.licenses_for_participant(requirement.participant_id, requirement.kind)
    usable = [
        license_
        for license_ in candidates
        if requirement.usage in license_.usages
        and license_.valid_from <= at
        and (license_.valid_to is None or at <= license_.valid_to)
        and not rights.is_withdrawn(license_.license_id, at)
    ]
    if usable:
        chosen = usable[0]
        return WatermarkEntry(requirement, chosen.license_id, "active", chosen.valid_to)
    if candidates:
        chosen = candidates[0]
        return WatermarkEntry(requirement, chosen.license_id, "limited", chosen.valid_to)
    return WatermarkEntry(requirement, None, "missing", None)


def freeze_version(
    assets: AssetRegistry,
    rights: RightsRegistry,
    *,
    version_id: str,
    project_id: str,
    form: Form,
    segment_ids: tuple[str, ...],
    at: datetime,
) -> EpisodeVersion:
    """冻结版本：固定引用片段，并记录每一项权利需求当前的水位。"""
    require_aware(at, "冻结时间")
    if not segment_ids:
        raise ValueError("版本至少引用一个片段")
    assets.project(project_id)
    requirements = build_requirements(assets, project_id, form, segment_ids)
    watermark = tuple(_watermark_entry(rights, requirement, at) for requirement in requirements)
    return EpisodeVersion(
        version_id=version_id,
        project_id=project_id,
        form=form,
        segment_ids=tuple(segment_ids),
        requirements=requirements,
        watermark=watermark,
        frozen_at=at,
    )


class VersionStore:
    """版本库：冻结版本只增不改。"""

    def __init__(self) -> None:
        self._versions: dict[str, EpisodeVersion] = {}

    def add(self, version: EpisodeVersion) -> EpisodeVersion:
        if version.version_id in self._versions:
            raise ValueError("版本已存在")
        self._versions[version.version_id] = version
        return version

    def get(self, version_id: str) -> EpisodeVersion:
        try:
            return self._versions[version_id]
        except KeyError:
            raise KeyError(f"版本不存在：{version_id}") from None

    def all(self) -> list[EpisodeVersion]:
        return list(self._versions.values())

    def versions_with_segment(self, segment_id: str) -> list[EpisodeVersion]:
        return [
            version
            for version in self._versions.values()
            if segment_id in version.segment_ids
        ]
