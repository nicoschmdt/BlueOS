import asyncio
import json
from typing import Any, AsyncGenerator, List, cast

from commonwealth.utils.zenoh_helper import ZenohRouter
from extension.extension import Extension
from loguru import logger


class ExtensionHandlers:
    def __init__(self, router: ZenohRouter) -> None:
        self.router = router

    async def install_handler(
        self, identifier: str, tag: str = "", stable: str = "true"
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Install an extension and stream Docker pull progress. Each yielded value becomes a
        separate zenoh query reply; the stream ends when the install completes.
        """
        if tag:
            extension = cast(Extension, await Extension.from_manifest(identifier, tag))
        else:
            extension = await Extension.from_latest(identifier, stable.lower() == "true")

        async for chunk in extension.install():
            try:
                payload = json.loads(chunk)
                payload["identifier"] = identifier
                yield payload
            except Exception as e:
                logger.debug(f"Failed to process install progress chunk: {e}")

    async def uninstall_handler(self, identifier: str, tag: str = "") -> dict[str, str]:
        """
        Uninstall all versions of an extension by its identifier or just a specific version if a tag is provided.
        """
        if tag:
            extension = cast(Extension, await Extension.from_settings(identifier, tag))
            await extension.uninstall()
        else:
            extensions = cast(List[Extension], await Extension.from_settings(identifier))
            await asyncio.gather(*[ext.uninstall() for ext in extensions])
        return {"status": "success"}

    async def enable_handler(self, identifier: str, tag: str) -> dict[str, str]:
        """
        Enables an extension by its identifier and tag, remember that this will disable the current enabled extension.
        """
        extension = cast(Extension, await Extension.from_settings(identifier, tag))
        await extension.enable()
        return {"status": "success"}

    async def disable_handler(self, identifier: str) -> dict[str, str]:
        """
        Disables current running extension by its identifier.
        """
        extension = await Extension.from_running(identifier)
        await extension.disable()
        return {"status": "success"}

    async def restart_handler(self, identifier: str) -> dict[str, str]:
        """
        Restart current running extension by its identifier.
        """
        extension = await Extension.from_running(identifier)
        await extension.restart()
        return {"status": "success"}

    async def fetch_handler(self) -> list[dict[str, Any]]:
        """
        List details of all installed extensions.
        """
        extensions = cast(List[Extension], await Extension.from_settings())
        return [ext.source.dict() for ext in extensions if ext.source.identifier != ""]

    async def keep_uploaded_extension_alive_handler(self, temp_tag: str) -> None:
        """
        Refresh the keep-alive timestamp for a temporary extension while the user is editing metadata.
        """
        Extension.keep_temporary_extension_alive(temp_tag)

    async def upload_handler(self, content: bytes) -> AsyncGenerator[dict[str, Any], None]:
        """
        Load a Docker image from an uploaded tar, inspect it, and create a temporary extension.
        Each yielded value becomes a separate zenoh query reply: intermediate phases carry a
        `phase` key, the final reply carries the extension metadata.
        """
        if not content:
            raise ValueError("Empty tar payload")

        yield {"phase": "loading"}
        image_name = await Extension.load_image_from_tar(content)
        yield {"phase": "inspecting"}
        metadata = await Extension.inspect_image_labels(image_name)
        yield {"phase": "finalizing"}
        temp_extension = await Extension.create_temporary_extension(image_name, metadata)
        yield {
            "temp_tag": temp_extension.tag,
            "metadata": metadata,
            "image_name": image_name,
        }

    def register_queryables(self) -> None:
        self.router.add_queryable("extension/fetch", self.fetch_handler)
        self.router.add_queryable("extension/install", self.install_handler)
        self.router.add_queryable("extension/uninstall", self.uninstall_handler)
        self.router.add_queryable("extension/enable", self.enable_handler)
        self.router.add_queryable("extension/disable", self.disable_handler)
        self.router.add_queryable("extension/restart", self.restart_handler)
        self.router.add_queryable("extension/upload/keep-alive", self.keep_uploaded_extension_alive_handler)
        self.router.add_queryable("extension/upload", self.upload_handler, has_payload=True)
