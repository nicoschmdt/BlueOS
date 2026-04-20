from commonwealth.utils.zenoh_helper import ZenohRouter
from manifest import Manifest, ManifestManager

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

    def register_queryables(self) -> None:
        self.router.add_queryable("manifest", self.fetch_handler)
