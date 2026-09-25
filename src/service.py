"""网络音频内容资产服务门面：把谱系、权利、版本、门禁与账本连成一体。"""

from __future__ import annotations

from datetime import datetime

from .assets import AssetRegistry
from .gate import DistributionGate
from .ledger import Ledger
from .rights import RightsRegistry
from .versions import EpisodeVersion, Form, VersionStore, freeze_version


class ContentAssetService:
    """内容资产服务：统一登记、冻结、审核、发布与结算的入口。"""

    def __init__(self) -> None:
        self.assets = AssetRegistry()
        self.rights = RightsRegistry(self.assets)
        self.versions = VersionStore()
        self.gate = DistributionGate(self.assets, self.rights, self.versions)
        self.ledger = Ledger()

    def freeze_version(
        self,
        *,
        version_id: str,
        project_id: str,
        form: Form,
        segment_ids: tuple[str, ...],
        at: datetime,
    ) -> EpisodeVersion:
        """冻结成品版本：固定引用片段与权利水位，冻结后不可更改。"""
        version = freeze_version(
            self.assets,
            self.rights,
            version_id=version_id,
            project_id=project_id,
            form=form,
            segment_ids=segment_ids,
            at=at,
        )
        return self.versions.add(version)
