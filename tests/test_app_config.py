from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import Request
from fastapi.testclient import TestClient

from config.manager import load_config, resolve_data_dir, save_config
from config.schema import (
    AppConfig,
    ChannelsConfig,
    EmbeddingConfig,
    FeishuConfig,
    OpenAIClientConfig,
    StorageConfig,
)
from config.web import _is_local_request
from gateway.app import app, resolve_feishu_runtime_config


class ConfigManagerTests(unittest.TestCase):
    def test_round_trip_includes_storage_and_uses_atomic_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            expected = AppConfig(
                client=OpenAIClientConfig(
                    api_key="secret",
                    base_url="https://example.test/v1",
                    model_name="example-model",
                ),
                embedding=EmbeddingConfig(
                    model_name="qwen3-embedding",
                    base_url="https://embedding.example.test/v1",
                    api_key="embedding-secret",
                ),
                storage=StorageConfig(data_dir=str(Path(directory) / "data")),
                channels=ChannelsConfig(
                    feishu=FeishuConfig(
                        enabled=True,
                        app_id="cli_test",
                        app_secret="feishu-secret",
                    ),
                ),
            )

            self.assertEqual(save_config(expected, path), path)
            actual = load_config(path)

            self.assertEqual(actual, expected)
            self.assertFalse((path.parent / ".config.json.tmp").exists())

    def test_legacy_config_gets_default_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.json"
            path.write_text(
                json.dumps({"client": {"model_name": "legacy-model"}}),
                encoding="utf-8",
            )

            config = load_config(path)

            self.assertEqual(config.client.model_name, "legacy-model")
            self.assertEqual(config.embedding.model_name, "qwen3-embedding")
            self.assertIsNone(config.embedding.base_url)
            self.assertEqual(config.storage.data_dir, "~/.novicesynapse")
            self.assertFalse(config.channels.feishu.enabled)
            self.assertEqual(config.channels.feishu.app_id, "")

    def test_environment_data_dir_overrides_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(storage=StorageConfig(data_dir=str(Path(directory) / "file")))
            override = str(Path(directory) / "environment")

            with patch.dict(os.environ, {"NOVICESYNAPSE_DATA_DIR": override}):
                self.assertEqual(resolve_data_dir(config), Path(override).resolve())

    def test_feishu_runtime_reads_saved_config_and_environment_takes_precedence(self) -> None:
        config = AppConfig(
            channels=ChannelsConfig(
                feishu=FeishuConfig(
                    enabled=True,
                    app_id="cli_saved",
                    app_secret="saved-secret",
                ),
            ),
        )

        self.assertEqual(
            resolve_feishu_runtime_config(config),
            (True, "cli_saved", "saved-secret"),
        )
        with patch.dict(
            os.environ,
            {
                "FEISHU_APP_ID": "cli_environment",
                "FEISHU_APP_SECRET": "environment-secret",
            },
        ):
            self.assertEqual(
                resolve_feishu_runtime_config(config),
                (True, "cli_environment", "environment-secret"),
            )


class ConfigWebTests(unittest.TestCase):
    def test_tutorial_completion_is_persisted_once_per_local_user_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config_file = Path(directory) / "config.json"
            with patch.dict(os.environ, {"NOVICESYNAPSE_CONFIG_FILE": str(config_file)}):
                client = TestClient(app)
                before = client.get("/api/tutorial/status")
                completed = client.post("/api/tutorial/complete")
                after = client.get("/api/tutorial/status")

            self.assertEqual(before.status_code, 200)
            self.assertFalse(before.json()["completed"])
            self.assertEqual(completed.status_code, 200)
            self.assertTrue(after.json()["completed"])
            self.assertTrue((Path(directory) / ".seefurther-tutorial-v2-complete").is_file())

    def test_generation_snapshot_requires_matching_session(self) -> None:
        client = TestClient(app)

        missing_session = client.get("/chat/generations/example")
        self.assertEqual(missing_session.status_code, 422)

        with patch("gateway.app.get_stream_generation", return_value=None) as get_snapshot:
            not_found = client.get("/chat/generations/example?session_id=session-a")

        self.assertEqual(not_found.status_code, 404)
        get_snapshot.assert_called_once_with("example", session_id="session-a")

    def test_api_masks_secret_and_reports_effective_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                client=OpenAIClientConfig(api_key="never-return-this", model_name="model"),
                storage=StorageConfig(data_dir=directory),
                channels=ChannelsConfig(
                    feishu=FeishuConfig(
                        enabled=True,
                        app_id="cli_public_id",
                        app_secret="never-return-feishu-secret",
                    ),
                ),
            )
            with patch("config.web.load_config", return_value=config):
                response = TestClient(app).get("/api/config")

            self.assertEqual(response.status_code, 200)
            body = response.json()
            self.assertTrue(body["client"]["api_key_configured"])
            self.assertEqual(body["embedding"]["model_name"], "qwen3-embedding")
            self.assertTrue(body["embedding"]["uses_client_base_url"])
            self.assertNotIn("never-return-this", response.text)
            self.assertEqual(body["storage"]["effective_data_dir"], str(Path(directory).resolve()))
            self.assertTrue(body["channels"]["feishu"]["enabled"])
            self.assertEqual(body["channels"]["feishu"]["app_id"], "cli_public_id")
            self.assertTrue(body["channels"]["feishu"]["app_secret_configured"])
            self.assertNotIn("never-return-feishu-secret", response.text)

    def test_update_preserves_blank_secret_and_creates_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "research-data"
            config = AppConfig(
                client=OpenAIClientConfig(api_key="existing-secret", model_name="old"),
            )
            with (
                patch("config.web.load_config", return_value=config),
                patch("config.web.save_config") as save,
            ):
                response = TestClient(app).put(
                    "/api/config",
                    json={
                        "base_url": "https://example.test/v1/",
                        "model_name": "new-model",
                        "embedding_model_name": "custom-embedding",
                        "embedding_base_url": "https://embedding.example.test/v1/",
                        "data_dir": str(target),
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["restart_required"])
            self.assertEqual(config.client.api_key, "existing-secret")
            self.assertEqual(config.client.base_url, "https://example.test/v1")
            self.assertEqual(config.client.model_name, "new-model")
            self.assertEqual(config.embedding.model_name, "custom-embedding")
            self.assertEqual(
                config.embedding.base_url,
                "https://embedding.example.test/v1",
            )
            self.assertEqual(config.storage.data_dir, str(target))
            self.assertTrue(target.is_dir())
            save.assert_called_once_with(config)

    def test_update_feishu_preserves_blank_secret_and_requires_restart(self) -> None:
        config = AppConfig(
            channels=ChannelsConfig(
                feishu=FeishuConfig(
                    enabled=False,
                    app_id="cli_old",
                    app_secret="existing-secret",
                ),
            ),
        )
        app.state.reload_runtime_config = lambda updated: {"runtime_reloaded": True}
        try:
            with (
                patch("config.web.load_config", return_value=config),
                patch("config.web.save_config") as save,
            ):
                response = TestClient(app).put(
                    "/api/config",
                    json={
                        "feishu_enabled": True,
                        "feishu_app_id": "cli_new",
                    },
                )
        finally:
            del app.state.reload_runtime_config

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["channels_restart_required"])
        self.assertTrue(response.json()["restart_required"])
        self.assertEqual(config.channels.feishu.app_id, "cli_new")
        self.assertEqual(config.channels.feishu.app_secret, "existing-secret")
        save.assert_called_once_with(config)

    def test_enabling_feishu_requires_complete_credentials(self) -> None:
        config = AppConfig()
        with (
            patch("config.web.load_config", return_value=config),
            patch("config.web.save_config") as save,
        ):
            response = TestClient(app).put(
                "/api/config",
                json={"feishu_enabled": True, "feishu_app_id": "cli_only"},
            )

        self.assertEqual(response.status_code, 422)
        self.assertIn("App Secret", response.json()["detail"])
        save.assert_not_called()

    def test_configuration_page_is_available(self) -> None:
        response = TestClient(app).get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-seefurther-entry="settings"', response.text)
        settings_html = (
            Path(__file__).resolve().parents[1] / "gateway/static/settings/index.html"
        ).read_text(encoding="utf-8")
        settings_script = (
            Path(__file__).resolve().parents[1] / "gateway/static/settings/app.js"
        ).read_text(encoding="utf-8")
        manager_source = (
            Path(__file__).resolve().parents[1] / "config/manager.py"
        ).read_text(encoding="utf-8")
        self.assertIn('id="embedding-model-name"', settings_html)
        self.assertIn('id="embedding-base-url"', settings_html)
        self.assertIn('id="embedding-api-key"', settings_html)
        self.assertIn('data-config-tab="model"', settings_html)
        self.assertIn('data-config-tab="channels"', settings_html)
        self.assertIn('id="feishu-enabled"', settings_html)
        self.assertIn('id="feishu-app-id"', settings_html)
        self.assertIn('id="feishu-app-secret"', settings_html)
        self.assertIn("使用长连接接收事件", settings_html)
        self.assertIn("im.message.receive_v1", settings_html)
        self.assertIn("im:message", settings_html)
        self.assertIn("im:message.p2p_msg:readonly", settings_html)
        self.assertIn("创建版本并发布最新版本", settings_html)
        self.assertIn("channels_restart_required", settings_script)
        self.assertNotIn("MinerU", settings_html + settings_script)
        self.assertEqual(settings_html.count('class="optional-config"'), 1)
        self.assertLess(settings_html.index('id="embedding-base-url"'), settings_html.index('id="embedding-api-key"'))
        self.assertLess(settings_html.index('id="embedding-api-key"'), settings_html.index('id="embedding-model-name"'))
        self.assertNotIn("配置文件：", settings_html)
        self.assertIn("三步完成配置", settings_html)
        self.assertIn("模型数据配置", settings_script)
        self.assertNotIn(r"C:\Users\sss", settings_html + settings_script + manager_source)
        self.assertIn("Path.home()", manager_source)

    def test_runtime_connection_changes_can_apply_without_restart(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                client=OpenAIClientConfig(api_key="secret", model_name="old-model"),
                storage=StorageConfig(data_dir=directory),
            )
            app.state.reload_runtime_config = lambda updated: {"runtime_reloaded": True}
            try:
                with (
                    patch("config.web.load_config", return_value=config),
                    patch("config.web.save_config"),
                ):
                    response = TestClient(app).put(
                        "/api/config",
                        json={
                            "model_name": "new-model",
                            "data_dir": directory,
                        },
                    )
            finally:
                del app.state.reload_runtime_config

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["runtime_reloaded"])
            self.assertFalse(response.json()["restart_required"])

    def test_remote_configuration_is_rejected_by_default(self) -> None:
        request = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/config",
                "headers": [],
                "client": ("192.168.10.8", 5000),
            }
        )
        self.assertFalse(_is_local_request(request))


if __name__ == "__main__":
    unittest.main()
