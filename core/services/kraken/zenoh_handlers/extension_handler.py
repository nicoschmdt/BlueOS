import asyncio
import json
from typing import Any, AsyncGenerator, List, cast

from commonwealth.utils.zenoh_helper import ZenohRouter
from extension.extension import Extension
from extension_logs import ExtensionLogPublisher
from harbor import ContainerManager
from loguru import logger
from settings import get_extension_settings


class ExtensionHandlers:
    INSTALL_PROGRESS_TOPIC = "kraken/extension/install/progress"

    def __init__(self, router: ZenohRouter) -> None:
        self.router = router

    async def logs_request_handler(self, extension_name: str) -> dict[str, Any]:
        if not extension_name:
            return {"error": "extension_name parameter is required"}

        try:
            extensions = get_extension_settings()
            extension = next((ext for ext in extensions if extension_name in (ext.identifier, ext.name)), None)

            if not extension:
                return {"error": f"Extension {extension_name} not found"}

            if not extension.enabled:
                return {"error": f"Extension {extension_name} is not enabled"}

            topic = ExtensionLogPublisher._topic_for(extension)

            container_name = extension.container_name()

            raw_logs = await ContainerManager.get_container_historical_logs(container_name)
            formatted_messages = []
            for raw_line in raw_logs:
                level, _ = ExtensionLogPublisher._extract_level(raw_line)
                formatted_messages.append(
                    {
                        "level": level,
                        "message": raw_line,
                    }
                )
            return {
                "status": "success",
                "messages": formatted_messages,
                "total_lines": len(formatted_messages),
                "topic": topic,
            }
        except Exception as e:
            logger.exception(f"Error handling logs request for {extension_name}")
            return {"error": str(e), "error_type": type(e).__name__}

    @staticmethod
    async def _install_progress_stream(identifier: str, extension: Extension) -> AsyncGenerator[str, None]:
        async for chunk in extension.install():
            try:
                payload = json.loads(chunk)
                payload["identifier"] = identifier
                yield handle_json(payload)
            except Exception as e:
                logger.debug(f"Failed to process install progress chunk: {e}")

    async def install_handler(self, identifier: str, tag: str = "", stable: str = "true") -> dict[str, str]:
        if tag:
            extension = cast(Extension, await Extension.from_manifest(identifier, tag))
        else:
            extension = await Extension.from_latest(identifier, stable.lower() == "true")

        self.router.publish_from_generator(
            self.INSTALL_PROGRESS_TOPIC,
            self._install_progress_stream(identifier, extension),
            on_complete=handle_json({"identifier": identifier, "status": "complete"}),
        )
        return {"status": "started"}

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

    def register_queryables(self) -> None:
        self.router.add_queryable("extension", self.fetch_handler)
        self.router.add_queryable("extension/logs/request", self.logs_request_handler)
        self.router.add_queryable("extension/install", self.install_handler)
        self.router.add_queryable("extension/uninstall", self.uninstall_handler)
        self.router.add_queryable("extension/enable", self.enable_handler)
        self.router.add_queryable("extension/disable", self.disable_handler)
        self.router.add_queryable("extension/restart", self.restart_handler)


def handle_json(data: Any) -> str:
    try:
        return json.dumps(data, default=str)
    except (TypeError, ValueError) as e:
        logger.error(f"Error serializing data to JSON: {data}, {e}")
        return '{"error": "Serialization failed"}'
