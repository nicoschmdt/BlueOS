from commonwealth.utils.zenoh_helper import ZenohSession


ZENOH_SESSIONS: dict[str, ZenohSession] = {}


def create_zenoh_session(service_name: str) -> ZenohSession:
    zenoh_session = ZenohSession(service_name)
    ZENOH_SESSIONS[service_name] = zenoh_session
    return zenoh_session


def get_zenoh_session(service_name: str) -> ZenohSession:
    if service_name in ZENOH_SESSIONS:
        return ZENOH_SESSIONS[service_name]
    return create_zenoh_session(service_name)


def close_zenoh_session(service_name: str) -> None:
    ZENOH_SESSIONS[service_name].close()
    del ZENOH_SESSIONS[service_name]
