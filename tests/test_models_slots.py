"""The four model slots chosen through the app layer (the Models contract, 0.1.41).

``PUT /api/connections/gui`` takes a catalogue id as ``PUT /api/connections/llm`` does;
``PUT /api/connections/image`` and ``/video`` write the ``[image]`` / ``[video]`` slots; and
for a slot nobody chose the runtime follows the contract's order — the chat provider's own
model when it can, the account's when signed in. ``MockLLM`` only; the relay is a key in the
vault and a stubbed model list, nothing on the network.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nanomuse.cloud import CloudClient, model_url
from nanomuse.config import Settings
from nanomuse.llm import MockLLM
from nanomuse.server import create_app
from nanomuse.server.service import MuseService

BAILIAN = "https://dashscope.aliyuncs.com/compatible-mode/v1"
RELAY = "http://127.0.0.1:9"
RELAY_MODELS: list[dict[str, Any]] = [
    {"id": "qwen3.8-27b", "nanomuse": {"kind": "chat", "for": ["chat", "gui"], "recommended_for": ["gui"]}},
    {"id": "deepseek-v4.1-flash", "nanomuse": {"kind": "chat", "for": ["chat"], "recommended": True}},
    {"id": "qwen-image-3.0", "nanomuse": {"kind": "image"}},
    {"id": "wan2.2-i2v-flash", "nanomuse": {"kind": "video"}},
]  # fmt: skip


def _make(settings: Settings) -> tuple[TestClient, MuseService]:
    settings.server.token = "secret-token"
    service = MuseService(settings, llm=MockLLM([]))
    app = create_app(settings, service)
    client = TestClient(app)
    client.headers["Authorization"] = "Bearer secret-token"
    return client, service


def _sign_in(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """The account on this runtime: the Cloud key in the vault, the relay's list stubbed."""
    settings.hub.enabled = False
    settings.cloud.base_url = RELAY

    async def fake_models(self: CloudClient) -> list[dict[str, Any]]:
        return list(RELAY_MODELS)

    monkeypatch.setattr(CloudClient, "models", fake_models)


def _settings_file(settings: Settings) -> dict[str, Any]:
    return json.loads((settings.data_dir / "app-settings.json").read_text("utf-8"))


# ----------------------------------------------------------------------------- the hands
def test_gui_accepts_a_catalogue_id_like_the_chat_slot(settings: Settings) -> None:
    client, service = _make(settings)
    with client:
        view = client.put(
            "/api/connections/gui",
            json={"provider": "bailian", "model": "qwen3.8-27b", "api_key": "sk-hands"},
        ).json()
        assert view["provider"] == "bailian" and view["provider_id"] == "bailian"
        assert view["model"] == "qwen3.8-27b" and view["key_source"] == "vault"
        assert view["effective_model"] == "qwen3.8-27b" and view["effective_source"] == "gui"
        # the id brings the catalogue's endpoint; the key is a vault reference
        assert settings.gui.endpoint == BAILIAN
        saved = _settings_file(settings)["gui"]
        assert saved["provider"] == "bailian" and saved["api_key"] == "{{vault:GUI_API_KEY}}"
        llm = getattr(service.app.make_gui_llm(), "inner", None) or service.app.make_gui_llm()
        assert llm.settings.base_url == BAILIAN and llm.settings.api_key == "sk-hands"
        hands = client.get("/api/providers").json()["configured"]["hands"]
        assert hands == {
            "provider": "bailian",
            "model": "qwen3.8-27b",
            "protocol": "openai",
            "source": "app",
        }
        # the raw forms still work, and a URL's host names the entry
        view = client.put(
            "/api/connections/gui",
            json={
                "provider": "openai",
                "base_url": "https://api.moonshot.cn/v1",
                "model": "kimi-k2.6",
            },
        ).json()
        assert view["provider"] == "openai" and view["provider_id"] == "moonshot"
        assert (
            client.put("/api/connections/gui", json={"provider": "openai_responses"}).status_code
            == 200
        )
        # ids the catalogue lacks, or whose protocol the runtime does not speak, are refused
        r = client.put("/api/connections/gui", json={"provider": "nope"})
        assert r.status_code == 400 and "unknown provider" in r.json()["detail"]
        r = client.put("/api/connections/gui", json={"provider": "anthropic"})
        assert r.status_code == 400 and "anthropic" in r.json()["detail"]
        assert "sk-hands" not in json.dumps(client.get("/api/connections").json())


def test_hands_follow_an_own_chat_provider_that_sees(settings: Settings) -> None:
    """Contract §3: with no ``[gui]`` model, the chat provider's own hands model when the
    chat provider is an own provider with vision — by id, and by the URL's host."""
    settings.llm.provider = "openai"
    settings.llm.base_url = BAILIAN
    settings.llm.model = "qwen3.7-plus"
    client, service = _make(settings)
    with client:
        assert service.app.hands_choice() == ("chat", "qwen3.8-27b")
        gui = client.get("/api/connections").json()["gui"]
        assert gui["effective_model"] == "qwen3.8-27b" and gui["effective_source"] == "chat"
        assert gui["default_model"] == "qwen3.8-27b" and gui["cloud"] is False
        hands = client.get("/api/providers").json()["configured"]["hands"]
        assert hands["provider"] == "bailian" and hands["model"] == "qwen3.8-27b"
        llm = getattr(service.app.make_gui_llm(), "inner", None) or service.app.make_gui_llm()
        assert llm.settings.model == "qwen3.8-27b" and llm.settings.base_url == BAILIAN
        assert llm.settings.api_key == "test"  # the chat model's key, same host
        # an explicit choice always wins
        client.put("/api/connections/gui", json={"model": "qwen3.7-max"})
        assert service.app.hands_choice() == ("gui", "qwen3.7-max")
        assert service.app.gui_model(default_only=True) == "qwen3.8-27b"

    by_id = settings.model_copy(deep=True)
    by_id.gui.model = ""
    by_id.llm.provider = "bailian"
    by_id.llm.base_url = None
    by_id.llm.model = "qwen3.7-plus"
    by_id.data_dir = settings.data_dir / "by-id"
    by_id.ensure_dirs()
    service2 = MuseService(by_id, llm=MockLLM([]))
    assert service2.app.hands_choice() == ("chat", "qwen3.8-27b")


def test_the_account_takes_the_hands_when_the_chat_provider_cannot_see(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract §3: a local model for the chat, the account signed in, nothing chosen for
    the hands — the relay's hands model under the account key; signed out, the chat model."""
    settings.llm.base_url = "http://127.0.0.1:11434/v1"
    settings.llm.model = "qwen3:8b"
    settings.llm.api_key = ""
    _sign_in(settings, monkeypatch)
    client, service = _make(settings)
    with client:
        assert service.app.hands_choice() == ("chat", "qwen3:8b")  # signed out: as before
        service.app.vault.set("NANOMUSE_CLOUD_KEY", "cloud-key")
        service.app.cloud_gui_model = "qwen3.8-27b"
        assert service.app.hands_choice() == ("cloud", "qwen3.8-27b")
        gui = client.get("/api/connections").json()["gui"]
        assert gui["effective_source"] == "cloud" and gui["effective_model"] == "qwen3.8-27b"
        assert gui["cloud"] is False  # the chat model is still the local one
        hands = client.get("/api/providers").json()["configured"]["hands"]
        assert hands["provider"] == "nanomuse_cloud" and hands["source"] == "cloud"
        llm = getattr(service.app.make_gui_llm(), "inner", None) or service.app.make_gui_llm()
        assert llm.settings.base_url == model_url(RELAY) and llm.settings.api_key == "cloud-key"
        assert llm.settings.model == "qwen3.8-27b"
        # a host the catalogue does not list is whatever the person installed: the chat model
        settings.llm.base_url = "http://gateway.internal:8000/v1"
        assert service.app.hands_choice() == ("chat", "qwen3:8b")


# ----------------------------------------------------------------------------- pictures, clips
def test_image_and_video_slots_round_trip(settings: Settings) -> None:
    settings.llm.base_url = "https://api.deepseek.com"  # draws nothing by itself
    client, service = _make(settings)
    with client:
        before = client.get("/api/connections").json()
        assert (
            before["image"]["configured"] is False and before["image"]["effective_provider"] == ""
        )
        assert client.get("/api/providers").json()["configured"]["image"] is None

        image = client.put(
            "/api/connections/image", json={"provider": "zhipu", "api_key": "sk-img"}
        ).json()
        assert image["provider"] == "zhipu" and image["provider_id"] == "zhipu"
        assert image["model"] == "" and image["key_source"] == "vault" and image["from_app"] is True
        # an empty model is the catalogue's default; the slot is the answer, from the app
        assert image["effective_provider"] == "zhipu" and image["effective_model"] == "glm-image"
        assert image["effective_source"] == "app"
        assert client.get("/api/connections/image").json() == image
        assert client.get("/api/connections").json()["image"] == image
        saved = _settings_file(settings)
        assert saved["image"] == {"provider": "zhipu", "api_key": "{{vault:IMAGE_API_KEY}}"}
        assert service.app.vault.get("IMAGE_API_KEY") == "sk-img"
        assert (
            settings.image.provider == "zhipu"
            and settings.image.api_key == "{{vault:IMAGE_API_KEY}}"
        )
        ep = service.avatar.endpoint()
        assert ep is not None and ep.base_url == "https://open.bigmodel.cn/api/paas/v4"
        assert ep.api_key == "sk-img" and ep.image_model == "glm-image" and not ep.clips

        video = client.put(
            "/api/connections/video",
            json={"provider": "bailian", "model": "wan2.2-i2v-flash", "api_key": "sk-vid"},
        ).json()
        assert video["provider"] == "bailian" and video["model"] == "wan2.2-i2v-flash"
        assert video["effective_provider"] == "bailian" and video["effective_source"] == "app"
        assert client.get("/api/connections/video").json() == video
        assert _settings_file(settings)["video"]["api_key"] == "{{vault:VIDEO_API_KEY}}"
        ep = service.avatar.endpoint()
        assert ep is not None and ep.clips and ep.video_host == "https://dashscope.aliyuncs.com"
        assert ep.video_key == "sk-vid" and ep.api_key == "sk-img"  # each host its own key

        view = client.get("/api/providers").json()
        assert view["configured"]["image"]["provider"] == "zhipu"
        assert view["configured"]["image"]["model"] == "glm-image"
        assert view["configured"]["image"]["source"] == "app"
        assert view["configured"]["video"]["provider"] == "bailian"
        assert view["configured"]["video"]["model"] == "wan2.2-i2v-flash"
        zhipu = next(p for p in view["providers"] if p["id"] == "zhipu")
        assert zhipu["configured"] == ["image"]
        assert next(p for p in view["providers"] if p["id"] == "bailian")["configured"] == ["video"]
        assert "image" in view["capabilities"] and "video" in view["capabilities"]
        assert view["unavailable"]["image"] == "" and view["unavailable"]["video"] == ""
        assert "sk-img" not in json.dumps(view) and "sk-vid" not in json.dumps(view)
        assert "sk-img" not in json.dumps(client.get("/api/connections").json())

        # a bare URL for a host the catalogue does not list, and a model there
        other = client.put(
            "/api/connections/image",
            json={
                "provider": "openai",
                "base_url": "https://images.example.com",
                "model": "flux-2",
            },
        ).json()
        assert other["base_url"] == "https://images.example.com/v1" and other["provider_id"] == ""
        assert other["effective_model"] == "flux-2"
        assert client.get("/api/providers").json()["configured"]["image"]["provider"] == "custom"

        # a reference to a key the vault already holds (the account's) is kept as written
        service.app.vault.set("NANOMUSE_CLOUD_KEY", "nmc-key")
        ref = client.put(
            "/api/connections/image",
            json={"provider": "bailian", "api_key": "{{vault:NANOMUSE_CLOUD_KEY}}"},
        ).json()
        assert ref["key_source"] == "vault"
        assert _settings_file(settings)["image"]["api_key"] == "{{vault:NANOMUSE_CLOUD_KEY}}"
        assert service.app.vault.get("IMAGE_API_KEY") == "sk-img"  # untouched
        ep = service.avatar.endpoint()
        assert ep is not None and ep.api_key == "nmc-key"
        missing = client.put(
            "/api/connections/image", json={"api_key": "{{vault:NOBODY_HAS_THIS}}"}
        ).json()
        assert missing["key_source"] == "missing"
        service.app.vault.delete("NANOMUSE_CLOUD_KEY")  # signed out again

        # clearing: all four empty, and the slot is gone from the file and the vault
        cleared = client.put(
            "/api/connections/image",
            json={"provider": "", "model": "", "base_url": "", "api_key": ""},
        ).json()
        assert cleared["configured"] is False and cleared["from_app"] is False
        assert cleared["key_source"] == "none" and cleared["effective_provider"] == ""
        assert "image" not in _settings_file(settings)
        assert service.app.vault.get("IMAGE_API_KEY") is None
        assert settings.image.configured is False
        assert client.get("/api/providers").json()["configured"]["image"] is None
        # the clips slot stands on its own
        assert client.get("/api/connections/video").json()["provider"] == "bailian"


def test_image_and_video_slots_refuse_what_the_catalogue_rules_out(settings: Settings) -> None:
    client, service = _make(settings)
    with client:
        r = client.put("/api/connections/image", json={"provider": "deepseek"})
        assert r.status_code == 400 and "DeepSeek has no image models" in r.json()["detail"]
        assert "Pictures need a provider with image models" in r.json()["detail"]
        r = client.put("/api/connections/video", json={"provider": "zhipu"})
        assert r.status_code == 400 and "has no video models" in r.json()["detail"]
        r = client.put("/api/connections/image", json={"provider": "chatgpt"})
        assert r.status_code == 400 and "ChatGPT sign-in" in r.json()["detail"]
        r = client.put("/api/connections/image", json={"provider": "nope"})
        assert r.status_code == 400 and "unknown provider" in r.json()["detail"]
        r = client.put("/api/connections/image", json={"provider": "anthropic"})
        assert r.status_code == 400
        # a URL whose host the catalogue knows cannot draw
        r = client.put("/api/connections/image", json={"base_url": "https://api.deepseek.com/v1"})
        assert r.status_code == 400 and "DeepSeek" in r.json()["detail"]
        # nothing was written
        assert "image" not in _settings_file(settings) and settings.image.configured is False
        assert TestClient(client.app).put("/api/connections/image", json={}).status_code == 401


def test_pictures_follow_the_chat_provider_then_the_account(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract §3 for pictures and clips with nothing chosen: the chat provider's own
    picture model when it has one; else the relay when signed in; else nothing."""
    settings.llm.base_url = "https://api.deepseek.com"
    _sign_in(settings, monkeypatch)
    client, service = _make(settings)
    with client:
        # signed out, DeepSeek for the chat: nothing draws
        assert service.avatar.endpoint() is None
        assert client.get("/api/providers").json()["configured"]["image"] is None
        # signed in: the account draws and makes the clips
        service.app.vault.set("NANOMUSE_CLOUD_KEY", "cloud-key")
        service.hub.models = list(RELAY_MODELS)
        ep = service.avatar.endpoint()
        assert ep is not None and ep.cloud and ep.base_url == model_url(RELAY).rstrip("/")
        assert ep.api_key == "cloud-key" and ep.image_model == "qwen-image-3.0"
        assert ep.clips and ep.video_model == "wan2.2-i2v-flash" and ep.video_key == "cloud-key"
        view = client.get("/api/providers").json()
        assert view["configured"]["image"] == {
            "provider": "nanomuse_cloud",
            "model": "qwen-image-3.0",
            "protocol": "openai",
            "source": "cloud",
        }
        assert view["configured"]["video"]["provider"] == "nanomuse_cloud"
        conn = client.get("/api/connections").json()
        assert conn["image"]["effective_source"] == "cloud" and conn["image"]["configured"] is False
        assert conn["video"]["effective_model"] == "wan2.2-i2v-flash"
        # the hands stay with DeepSeek, which sees: Cloud never jumps ahead of an own provider
        assert view["configured"]["hands"]["provider"] == "deepseek"

        # an own chat provider with pictures but no clips: its picture model, the relay's clips
        settings.llm.base_url = "https://api.openai.com/v1"
        settings.llm.model = "gpt-5.4-mini"
        ep = service.avatar.endpoint()
        assert ep is not None and not ep.cloud and ep.base_url == "https://api.openai.com/v1"
        assert ep.image_model == "gpt-image-2.5-flare" and ep.api_key == "test"
        assert ep.clips and ep.video_cloud and ep.video_host == RELAY
        assert ep.video_key == "cloud-key" and ep.video_model == "wan2.2-i2v-flash"
        view = client.get("/api/providers").json()
        assert view["configured"]["image"]["provider"] == "openai"
        assert view["configured"]["image"]["model"] == "gpt-image-2.5-flare"
        assert view["configured"]["video"]["provider"] == "nanomuse_cloud"
        conn = client.get("/api/connections").json()
        assert conn["image"]["effective_source"] == "chat"
        assert conn["video"]["effective_source"] == "cloud"

        # an explicit choice always wins over the account
        client.put("/api/connections/image", json={"provider": "zhipu", "api_key": "sk-img"})
        ep = service.avatar.endpoint()
        assert ep is not None and not ep.cloud and ep.image_model == "glm-image"
        assert ep.video_cloud  # clips still the account's: zhipu makes none
        client.put("/api/connections/video", json={"provider": "bailian", "api_key": "sk-vid"})
        ep = service.avatar.endpoint()
        assert ep is not None and not ep.video_cloud and ep.video_key == "sk-vid"
        assert ep.video_model == "wan2.2-i2v-flash"
        assert client.get("/api/providers").json()["configured"]["video"]["provider"] == "bailian"


# ----------------------------------------------------------------------------- the switch
def test_cloud_models_off_takes_the_account_out_of_every_automatic_rung(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """*Use NanoLika Cloud models* off (``[cloud] models``): the hands, the pictures and
    the clips no longer fall to the relay, the providers' listing and the chat picker leave
    the account out, a sign-in changes no slot; the sign-in itself stays. On again, the
    order is as before."""
    settings.llm.base_url = "http://127.0.0.1:11434/v1"  # a local model that cannot see
    settings.llm.model = "qwen3:8b"
    settings.llm.api_key = ""
    _sign_in(settings, monkeypatch)
    client, service = _make(settings)
    with client:
        service.app.vault.set("NANOMUSE_CLOUD_KEY", "cloud-key")
        service.app.cloud_gui_model = "qwen3.8-27b"
        service.hub.models = list(RELAY_MODELS)
        assert client.get("/api/cloud").json()["models"] is True
        assert service.app.hands_choice() == ("cloud", "qwen3.8-27b")
        assert service.avatar.endpoint() is not None
        assert "nanomuse_cloud" in client.get("/api/connections").json()["providers"]

        view = client.post("/api/cloud/models", json={"on": False}).json()
        assert view["models"] is False and view["signed_in"] is True
        assert _settings_file(settings)["cloud"]["models"] is False
        assert service.app.cloud_signed_in() and not service.app.cloud_models_on()
        # the hands: the chat model, as signed out; the studio: nothing draws
        assert service.app.hands_choice() == ("chat", "qwen3:8b")
        assert service.avatar.endpoint() is None
        providers = client.get("/api/providers").json()
        assert providers["configured"]["hands"]["provider"] != "nanomuse_cloud"
        assert providers["configured"]["image"] is None
        assert all(p["id"] != "nanomuse_cloud" for p in providers["providers"])
        assert "nanomuse_cloud" not in client.get("/api/connections").json()["providers"]
        # the account page still knows the account: the switch is not a sign-out
        assert service.app.vault.get("NANOMUSE_CLOUD_KEY") == "cloud-key"

        view = client.post("/api/cloud/models", json={"on": True}).json()
        assert view["models"] is True
        assert service.app.hands_choice() == ("cloud", "qwen3.8-27b")
        assert service.avatar.endpoint() is not None


def test_cloud_models_off_is_refused_while_the_chat_model_is_the_account(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The runtime holds one ``[llm]`` and no list of other keys, so the switch cannot move
    the chat elsewhere by itself: it says so (``409 chat_on_cloud``) until another chat
    model is picked. *Use as model* (the explicit ask) switches the models back on."""
    _sign_in(settings, monkeypatch)
    client, service = _make(settings)
    with client:
        service.app.vault.set("NANOMUSE_CLOUD_KEY", "cloud-key")
        settings.llm.base_url = model_url(RELAY)
        settings.llm.api_key = "{{vault:NANOMUSE_CLOUD_KEY}}"
        res = client.post("/api/cloud/models", json={"on": False})
        assert res.status_code == 409
        assert res.headers["X-Nanomuse-Code"] == "chat_on_cloud"
        assert settings.cloud.models is True
        # another chat model first, then the switch takes
        client.put(
            "/api/connections/llm",
            json={
                "provider": "openai",
                "base_url": "http://127.0.0.1:11434/v1",
                "model": "qwen3:8b",
                "api_key": "",
            },
        )
        assert client.post("/api/cloud/models", json={"on": False}).json()["models"] is False
        assert client.get("/api/connections").json()["llm"]["cloud"] is False
        # the explicit ask: the account is the chat model again and its models are back on
        llm = client.post("/api/cloud/use-as-model", json={}).json()
        assert llm["cloud"] is True
        assert settings.cloud.models is True
        assert client.get("/api/cloud").json()["models"] is True
