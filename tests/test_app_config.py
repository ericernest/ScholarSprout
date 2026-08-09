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
from config.schema import AppConfig, OpenAIClientConfig, StorageConfig
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
                        "data_dir": str(target),
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.json()["restart_required"])
            self.assertEqual(config.client.api_key, "existing-secret")
            self.assertEqual(config.client.base_url, "https://example.test/v1")
            self.assertEqual(config.client.model_name, "new-model")
            self.assertEqual(config.storage.data_dir, str(target))
            self.assertTrue(target.is_dir())
            save.assert_called_once_with(config)

    def test_configuration_page_is_available(self) -> None:
        response = TestClient(app).get("/settings")
        self.assertEqual(response.status_code, 200)
        self.assertIn("三步完成配置", response.text)

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
