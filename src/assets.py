"""内容资产谱系：从选题立项起登记参与者、素材与片段。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Role(Enum):
    """机构内的职责角色，用于信息裁剪与权限判断。"""

    CREATOR = "音频创作者"
    EDITOR = "节目编辑"
    RIGHTS_HOLDER = "嘉宾及权利人"
    LEGAL = "法务人员"
    BUSINESS = "商务人员"
    OPS = "平台运营人员"


class MaterialKind(Enum):
    RAW_RECORDING = "原始录制"
    TRANSCRIPT = "文字稿"
    EXTERNAL = "外部素材"
    MUSIC = "音乐"
    VOICE_SAMPLE = "声音样本"


class SegmentOrigin(Enum):
    HUMAN = "人工片段"
    SYNTHETIC = "合成片段"


def require_aware(moment: datetime, field: str) -> None:
    """领域时间一律要求携带时区，避免朴素时间与带时区时间比较出错。"""
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError(f"{field}必须携带时区信息")


@dataclass(frozen=True)
class Participant:
    participant_id: str
    display_name: str
    roles: frozenset[Role]


@dataclass(frozen=True)
class Project:
    """选题立项，是素材、片段、版本与合同的归属单位。"""

    project_id: str
    title: str
    participant_ids: tuple[str, ...]
    created_at: datetime


@dataclass(frozen=True)
class Material:
    """素材来源：原始录制、文字稿、外部素材、音乐或声音样本。"""

    material_id: str
    project_id: str
    kind: MaterialKind
    source: str
    owner_id: str
    participant_ids: tuple[str, ...]  # 素材中出声或出镜的当事人
    created_at: datetime


@dataclass(frozen=True)
class Segment:
    """人工或合成片段，记录引用的素材与合成内容标识。"""

    segment_id: str
    project_id: str
    origin: SegmentOrigin
    material_ids: tuple[str, ...]
    synthetic_label: str | None
    transcript_ref: str | None


class AssetRegistry:
    """资产谱系登记处：只增不改，登记后不可篡改。"""

    def __init__(self) -> None:
        self._participants: dict[str, Participant] = {}
        self._projects: dict[str, Project] = {}
        self._materials: dict[str, Material] = {}
        self._segments: dict[str, Segment] = {}

    def add_participant(self, participant: Participant) -> Participant:
        if participant.participant_id in self._participants:
            raise ValueError("参与者已存在")
        if not participant.roles:
            raise ValueError("参与者至少需要一个角色")
        self._participants[participant.participant_id] = participant
        return participant

    def create_project(self, project: Project) -> Project:
        if project.project_id in self._projects:
            raise ValueError("项目已存在")
        if not project.title.strip():
            raise ValueError("项目选题不能为空")
        if not project.participant_ids:
            raise ValueError("项目至少登记一名参与者")
        require_aware(project.created_at, "立项时间")
        for participant_id in project.participant_ids:
            self.participant(participant_id)
        self._projects[project.project_id] = project
        return project

    def add_material(self, material: Material) -> Material:
        if material.material_id in self._materials:
            raise ValueError("素材已存在")
        self.project(material.project_id)
        self.participant(material.owner_id)
        require_aware(material.created_at, "素材登记时间")
        for participant_id in material.participant_ids:
            self.participant(participant_id)
        self._materials[material.material_id] = material
        return material

    def add_segment(self, segment: Segment) -> Segment:
        if segment.segment_id in self._segments:
            raise ValueError("片段已存在")
        self.project(segment.project_id)
        if not segment.material_ids:
            raise ValueError("片段至少引用一个素材")
        for material_id in segment.material_ids:
            material = self.material(material_id)
            if material.project_id != segment.project_id:
                raise ValueError("片段引用的素材不属于同一项目")
        if segment.origin is SegmentOrigin.SYNTHETIC:
            if not (segment.synthetic_label or "").strip():
                raise ValueError("合成片段必须携带合成内容标识")
        elif segment.synthetic_label is not None:
            raise ValueError("人工片段不应携带合成内容标识")
        self._segments[segment.segment_id] = segment
        return segment

    def participant(self, participant_id: str) -> Participant:
        try:
            return self._participants[participant_id]
        except KeyError:
            raise KeyError(f"参与者不存在：{participant_id}") from None

    def project(self, project_id: str) -> Project:
        try:
            return self._projects[project_id]
        except KeyError:
            raise KeyError(f"项目不存在：{project_id}") from None

    def material(self, material_id: str) -> Material:
        try:
            return self._materials[material_id]
        except KeyError:
            raise KeyError(f"素材不存在：{material_id}") from None

    def segment(self, segment_id: str) -> Segment:
        try:
            return self._segments[segment_id]
        except KeyError:
            raise KeyError(f"片段不存在：{segment_id}") from None
