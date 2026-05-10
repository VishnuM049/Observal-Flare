from arq.connections import RedisSettings

from server.config import get_settings


def get_redis_settings() -> RedisSettings:
    settings = get_settings()
    url = settings.redis_url
    if url.startswith("redis://"):
        url = url[len("redis://"):]
    host, _, port_str = url.partition(":")
    port = int(port_str.split("/")[0]) if port_str else 6379
    database = int(url.split("/")[-1]) if "/" in url else 0
    return RedisSettings(host=host or "localhost", port=port, database=database)
