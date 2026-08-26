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
from config.schema import AppConfig, EmbeddingConfig, MinerUConfig, OpenAIClientConfig, StorageConfig
from config.web import _is_local_request
from gateway.app import app


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
                mineru=MinerUConfig(
                    base_url="https://mineru.example.test/file_parse",
                    api_key="mineru-secret",
                ),
                storage=StorageConfig(data_dir=str(Path(directory) / "data")),
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

    def test_environment_data_dir_overrides_config(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(storage=StorageConfig(data_dir=str(Path(directory) / "file")))
            override = str(Path(directory) / "environment")

            with patch.dict(os.environ, {"NOVICESYNAPSE_DATA_DIR": override}):
                self.assertEqual(resolve_data_dir(config), Path(override).resolve())


class ConfigWebTests(unittest.TestCase):
    def test_api_masks_secret_and_reports_effective_data_dir(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            config = AppConfig(
                client=OpenAIClientConfig(api_key="never-return-this", model_name="model"),
                storage=StorageConfig(data_dir=directory),
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

    def test_configuration_page_is_available(self) -> None:
        response = TestClient(app).get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn('data-paperaurora-entry="settings"', response.text)
        settings_html = (
            Path(__file__).resolve().parents[1] / "gateway/static/settings/index.html"
        ).read_text(encoding="utf-8")
        self.assertIn('id="embedding-model-name"', settings_html)
        self.assertIn('id="embedding-base-url"', settings_html)
        self.assertIn('id="embedding-api-key"', settings_html)
        self.assertIn('id="mineru-base-url"', settings_html)
        self.assertIn('id="mineru-api-key"', settings_html)
        self.assertEqual(settings_html.count('class="optional-config"'), 2)
        self.assertLess(settings_html.index('id="embedding-base-url"'), settings_html.index('id="embedding-api-key"'))
        self.assertLess(settings_html.index('id="embedding-api-key"'), settings_html.index('id="embedding-model-name"'))
        self.assertNotIn("配置文件：", settings_html)
        self.assertIn("三步完成配置", settings_html)

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
