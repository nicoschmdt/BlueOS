#!/usr/bin/env python

import argparse
import asyncio
import atexit
import json
import logging
import os
import time
import uuid
from datetime import datetime
from typing import Any, Generator, Optional

import aiohttp
import zenoh
from aiohttp import web
from commonwealth.utils.logs import InterceptHandler, init_logger
from commonwealth.utils.sentry_config import init_sentry_async
from commonwealth.utils.zenoh_helper import ZenohRouter, ZenohSession
from loguru import logger
from speedtest import Speedtest

SERVICE_NAME = "pardal"
SPEED_TEST: Optional[Speedtest] = None

parser = argparse.ArgumentParser(description="Pardal, web service to help with speed and latency tests")
parser.add_argument("-p", "--port", help="Port to run web server", action="store_true", default=9120)

args = parser.parse_args()

logging.basicConfig(handlers=[InterceptHandler()], level=0)

logger.info("Starting Pardal")

routes = web.RouteTableDef()

try:
    SPEED_TEST = Speedtest(secure=True)
except Exception:
    # When starting, the system may not be connected to the internet
    pass


async def websocket_echo(request: web.Request) -> web.WebSocketResponse:
    websocket = web.WebSocketResponse()
    await websocket.prepare(request)

    async for message in websocket:
        if message.type == aiohttp.WSMsgType.TEXT:
            await websocket.send_str(message.data)

    return websocket


async def get_file(request: web.Request) -> web.StreamResponse:
    size = int(request.rel_url.query.get("size", 100 * (2**20)))  # 100MB by default

    response = web.StreamResponse(status=200)
    response.headers["Content-Length"] = str(size)
    await response.prepare(request)

    for data_chunk in generate_random_data(size):
        await response.write(bytes(data_chunk))

    await response.write_eof()
    return response


async def post_file(request: web.Request) -> web.Response:
    while True:
        chunk = await request.content.readany()
        if not chunk:
            break
    return web.Response(status=200)


@routes.get("/internet_best_server")
async def internet_best_server(request: web.Request) -> web.Response:
    """
    Check internet best server for test from BlueOS.
    """
    # Since we are finding a new server, clear previous results
    # pylint: disable=global-statement

    interface_addr = request.query.get("interface_addr") or None

    global SPEED_TEST
    SPEED_TEST = Speedtest(secure=True, source_address=interface_addr)
    SPEED_TEST.get_best_server()
    return web.json_response(SPEED_TEST.results.dict())


# pylint: disable=unused-argument
@routes.get("/internet_download_speed")
async def internet_download_speed(request: web.Request) -> web.Response:
    """
    Check internet download speed test from BlueOS.
    """
    if not SPEED_TEST:
        raise RuntimeError("SPEED_TEST not initialized, initialize server search.")
    SPEED_TEST.download()
    return web.json_response(SPEED_TEST.results.dict())


# pylint: disable=unused-argument
@routes.get("/internet_upload_speed")
async def internet_upload_speed(request: web.Request) -> web.Response:
    """
    Check internet upload speed test from BlueOS.
    """
    if not SPEED_TEST:
        raise RuntimeError("SPEED_TEST not initialized, initialize server search.")
    SPEED_TEST.upload(pre_allocate=False)
    return web.json_response(SPEED_TEST.results.dict())


# pylint: disable=unused-argument
@routes.get("/internet_test_previous_result")
async def internet_test_previous_result(request: web.Request) -> web.Response:
    """
    Return previous result of internet speed test.
    """
    if not SPEED_TEST:
        raise RuntimeError("SPEED_TEST not initialized, initialize server search.")
    return web.json_response(SPEED_TEST.results.dict())


# pylint: disable=unused-argument
async def root(request: web.Request) -> web.Response:
    html_content = """
    <html>
        <head>
            <title>Pardal</title>
        </head>
    </html>
    """
    return web.Response(text=html_content, content_type="text/html")


##
zenoh_session = ZenohSession(SERVICE_NAME)
zenoh_router = ZenohRouter(SERVICE_NAME)

sessions: dict[str, dict[str, Any]] = {}


def request_handler(request: zenoh.Query) -> None:
    params = dict(request.parameters)  # type: ignore
    try:
        session_id = params.get("session_id")
    except KeyError:
        request.reply(request.selector.key_expr, json.dumps({"error": "Session ID is required"}))
        return

    method = params.get("method")
    if method == "upload":
        file_size = int(params.get("file_size"))
        upload_topic = f"pardal/upload/{session_id}/chunks"
        progress_topic = f"pardal/upload/{session_id}/progress"
        result_topic = f"pardal/upload/{session_id}/result"
        sessions[session_id] = {
            "session_id": session_id,
            "created_at": datetime.now().isoformat(),
            "start_time": None,
            "end_time": None,
            "bytes_received": 0,
            "expected_size": file_size,
            "chunks_received": 0,
            "upload_topic": upload_topic,
            "progress_topic": progress_topic,
            "result_topic": result_topic,
            "status": "waiting",  # waiting, receiving, complete, error
        }

        request.reply(request.selector.key_expr, json.dumps(sessions[session_id]))
        return
    if method == "download":
        logger.info(f"Downloading file for session {session_id}")
        get_file_z(session_id)
        return
    # elif method == "websocket":
    #     pass
    request.reply(request.selector.key_expr, json.dumps({"error": "Invalid method"}))


def start_session(query: zenoh.Query) -> None:
    session_id = str(uuid.uuid4())
    query.reply(query.selector.key_expr, json.dumps({"session_id": session_id}))


def generate_random_data(size: int, chunk_size: int = 1024 * 1024) -> Generator[bytes, None, None]:
    remaining_size = size
    while remaining_size > 0:
        yield os.urandom(min(chunk_size, remaining_size))
        remaining_size -= chunk_size


def handle_upload_chunk(sample: zenoh.Sample) -> None:

    if zenoh_session.session is None:
        return

    request_id = str(sample.key_expr).split("/")[2]

    if request_id not in sessions:
        logger.warning(f"Upload session {request_id} not found")
        return

    session = sessions[request_id]

    if session["status"] == "waiting":
        session["status"] = "receiving"
        session["start_time"] = time.time()
        logger.debug(f"Started receiving upload for session {request_id}")

    # Get chunk data
    chunk_data = sample.payload.to_bytes()
    chunk_size = len(chunk_data)

    # Update session stats
    session["bytes_received"] += chunk_size
    session["chunks_received"] += 1

    # Publish progress update
    current_time = time.time()
    elapsed = current_time - session["start_time"]
    speed_mbps = (session["bytes_received"] * 8) / (elapsed * 1_000_000) if elapsed > 0 else 0

    progress_data = {
        "session_id": request_id,
        "bytes_received": session["bytes_received"],
        "chunks_received": session["chunks_received"],
        "speed_mbps": speed_mbps,
        "progress_percent": (
            (session["bytes_received"] / session["expected_size"] * 100)
            if session["expected_size"] and session["expected_size"] > 0
            else None
        ),
        "timestamp": current_time,
    }

    zenoh_session.session.put(session["progress_topic"], json.dumps(progress_data))

    if session["expected_size"] and session["bytes_received"] >= session["expected_size"]:
        session["status"] = "complete"
        session["end_time"] = time.time()

        # Publish final result
        duration = session["end_time"] - session["start_time"]
        final_speed_mbps = (session["bytes_received"] * 8) / (duration * (2**20)) if duration > 0 else 0

        result_data = {
            "session_id": request_id,
            "bytes_received": session["bytes_received"],
            "chunks_received": session["chunks_received"],
            "duration_seconds": duration,
            "speed_mbps": final_speed_mbps,
            "completed_at": session["end_time"],
        }

        zenoh_session.session.put(session["result_topic"], json.dumps(result_data).encode("utf-8"))

        logger.info(f"Upload test complete for session {request_id}: {final_speed_mbps:.2f} Mbps")

    logger.debug(f"Received chunk {session['chunks_received']} for session {request_id}: {chunk_size} bytes")


def get_upload_status(query: zenoh.Query) -> None:
    request_id = str(query.key_expr).split("/")[2]
    if request_id not in sessions:
        query.reply(query.selector.key_expr, json.dumps({"status": "not_found"}))
        return
    query.reply(query.selector.key_expr, json.dumps(sessions[request_id]))


# pylint: disable=too-many-locals
def get_file_z(request_id: str) -> None:
    if zenoh_session.session is None:
        return

    chunk_topic = f"pardal/download/{request_id}/chunks"
    progress_topic = f"pardal/download/{request_id}/progress"
    result_topic = f"pardal/download/{request_id}/result"
    size = int(100 * (2**20))  # 100MB by default

    # Initialize session with numeric timestamps for calculations
    start_time = time.time()
    sessions[request_id] = {
        "session_id": request_id,
        "created_at": time.time(),
        "start_time": start_time,  # Use numeric timestamp
        "end_time": None,
        "status": "sending",
        "size": size,
        "bytes_sent": 0,
        "chunks_sent": 0,
        "progress_topic": progress_topic,
        "result_topic": result_topic,
    }

    # Publish chunks and track progress
    for chunk in generate_random_data(size):
        chunk_bytes = bytes(chunk)
        chunk_size = len(chunk_bytes)

        # Publish chunk
        zenoh_session.session.put(chunk_topic, chunk_bytes)

        # Update session stats
        sessions[request_id]["bytes_sent"] += chunk_size
        sessions[request_id]["chunks_sent"] += 1

        # Calculate and publish progress (every chunk or every N chunks)
        current_time = time.time()
        elapsed = current_time - start_time

        if elapsed > 0:
            speed_mbps = (sessions[request_id]["bytes_sent"] * 8) / (elapsed * (2**20))
        else:
            speed_mbps = 0

        # Publish progress update
        progress_data = {
            "session_id": request_id,
            "bytes_sent": sessions[request_id]["bytes_sent"],
            "chunks_sent": sessions[request_id]["chunks_sent"],
            "speed_mbps": speed_mbps,
            "progress_percent": (sessions[request_id]["bytes_sent"] / size * 100) if size > 0 else 0,
            "timestamp": datetime.now().isoformat(),
        }

        zenoh_session.session.put(progress_topic, json.dumps(progress_data).encode("utf-8"))

        # Small delay to allow network processing (optional)
        # time.sleep(0.001)  # 1ms delay between chunks

    # Finalize and publish result
    end_time = time.time()
    duration = end_time - start_time
    final_speed_mbps = (sessions[request_id]["bytes_sent"] * 8) / (duration * 1_000_000) if duration > 0 else 0

    sessions[request_id]["end_time"] = end_time
    sessions[request_id]["status"] = "completed"

    # Publish final result
    result_data = {
        "session_id": request_id,
        "bytes_sent": sessions[request_id]["bytes_sent"],
        "chunks_sent": sessions[request_id]["chunks_sent"],
        "duration_seconds": duration,
        "speed_mbps": final_speed_mbps,
        "completed_at": datetime.now().isoformat(),
    }

    zenoh_session.session.put(result_topic, json.dumps(result_data).encode("utf-8"))

    logger.info(f"Download test complete for session {request_id}: {final_speed_mbps:.2f} Mbps")


async def internet_best_server_z(interface_addr: Optional[str] = None) -> Any:
    """
    Check internet best server for test from BlueOS.
    """
    # Since we are finding a new server, clear previous results
    # pylint: disable=global-statement

    global SPEED_TEST
    SPEED_TEST = Speedtest(secure=True, source_address=interface_addr)
    SPEED_TEST.get_best_server()
    return SPEED_TEST.results.dict()


async def internet_download_speed_z() -> Any:
    """
    Check internet download speed test from BlueOS.
    """
    if not SPEED_TEST:
        raise RuntimeError("SPEED_TEST not initialized, initialize server search.")
    SPEED_TEST.download()
    return SPEED_TEST.results.dict()


async def internet_upload_speed_z() -> Any:
    """
    Check internet upload speed test from BlueOS.
    """
    if not SPEED_TEST:
        raise RuntimeError("SPEED_TEST not initialized, initialize server search.")
    SPEED_TEST.upload(pre_allocate=False)
    return SPEED_TEST.results.dict()


async def internet_test_previous_result_z() -> Any:
    """
    Return previous result of internet speed test.
    """
    if not SPEED_TEST:
        raise RuntimeError("SPEED_TEST not initialized, initialize server search.")
    return SPEED_TEST.results.dict()


if zenoh_session.session:
    zenoh_router.add_queryable("internet_download_speed", internet_download_speed_z)
    zenoh_router.add_queryable("internet_upload_speed", internet_upload_speed_z)
    zenoh_router.add_queryable("internet_test_previous_result", internet_test_previous_result_z)
    zenoh_router.add_queryable("internet_best_server", internet_best_server_z)
    zenoh_session.session.declare_queryable("pardal/start_session", start_session)
    zenoh_session.session.declare_queryable("pardal/request_handler", request_handler)
    zenoh_session.session.declare_queryable("pardal/operation/*/status", get_upload_status)
    zenoh_session.session.declare_subscriber("pardal/upload/*/chunks", handle_upload_chunk)


def cleanup() -> None:
    zenoh_session.close()


atexit.register(cleanup)
##


async def main() -> None:
    await init_sentry_async(SERVICE_NAME)

    app = web.Application()
    app.client_max_size = 2 * (2**30)  # 2 GBs

    app.add_routes([web.get("/ws", websocket_echo), *routes])
    app.router.add_get("/", root, name="root")
    app.router.add_get("/get_file", get_file, name="get_file")
    app.router.add_post("/post_file", post_file, name="post_file")

    init_logger(SERVICE_NAME)

    runner = web.AppRunner(app)
    await runner.setup()

    site = web.TCPSite(runner, host="0.0.0.0", port=args.port)
    await site.start()

    # Wait forever
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
