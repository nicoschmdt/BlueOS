from commonwealth.utils.zenoh_helper import ZenohRouter
from manifest import Manifest, ManifestManager
from manifest.models import RepositoryEntry

manifest_manager = ManifestManager.instance()


class ManifestHandlers:
    def __init__(self, router: ZenohRouter) -> None:
        self.router = router

    async def fetch_handler(self, data: bool = True, enabled: bool = False) -> list[Manifest]:
        """
        List all available manifests sorted by its priority. If data is set to false, only the manifest settings will be
        returned. If enabled is set to true, only enabled manifests will be returned.
        """
        return await manifest_manager.fetch(data, enabled)

    async def fetch_consolidated(self) -> list[RepositoryEntry]:
        """
        List a consolidation of all repository entries from all manifest sources merged by its sorted priority, if a
        repository entry is duplicated, the one with the highest priority will be kept.
        """
        return await manifest_manager.fetch_consolidated()

    async def enable_handler(self, identifier: str) -> dict[str, str]:
        """
        Enables a manifest source.
        """
        await manifest_manager.enable_source(identifier)
        return {"status": "success"}

    async def disable_handler(self, identifier: str) -> dict[str, str]:
        """
        Disables a manifest source.
        """
        await manifest_manager.disable_source(identifier)
        return {"status": "success"}

    def register_queryables(self) -> None:
        self.router.add_queryable("manifest/fetch", self.fetch_handler)
        self.router.add_queryable("manifest/consolidated", self.fetch_consolidated)
        self.router.add_queryable("manifest/enable", self.enable_handler)
        self.router.add_queryable("manifest/disable", self.disable_handler)
