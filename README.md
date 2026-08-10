# renson-healthbox-go

Fully asynchronous Python client for the unauthenticated local HTTP API of the
Renson Healthbox Go. The caller injects an `aiohttp.ClientSession`, making the
library suitable for Home Assistant and other asyncio applications.

```python
from aiohttp import ClientSession
from renson_healthbox_go import HealthboxGoApi

async with ClientSession() as session:
    client = HealthboxGoApi("192.168.1.50", session)
    info = await client.async_get_info()
    data = await client.async_update()
    await client.set_manual_override(50, 600)
```

This is an unofficial project and is not affiliated with Renson.

