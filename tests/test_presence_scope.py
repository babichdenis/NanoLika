import json
from types import SimpleNamespace

import pytest

from nanomuse.tools.devices import Devices


@pytest.mark.asyncio
async def test_offline_device_does_not_imply_messenger_delivery_is_offline():
    hub = SimpleNamespace(
        others=lambda: [{"name": "Phone", "kind": "phone", "online": False, "actions": []}],
        client=SimpleNamespace(connected=SimpleNamespace(is_set=lambda: True)),
        device_name="Computer",
    )
    result = await Devices(hub=hub)._run()
    data = json.loads(result.output)
    assert data["devices"][0]["online"] is False
    assert data["presence_scope"] == "app_control_connection_only"
    assert "not_allowed" in data["delivery_note"]
    assert "messenger" in data["delivery_note"]
