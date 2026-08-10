const form = document.querySelector("#settings-form");
const baseUrl = document.querySelector("#base-url");
const apiKey = document.querySelector("#api-key");
const modelName = document.querySelector("#model-name");
const embeddingModelName = document.querySelector("#embedding-model-name");
const embeddingBaseUrl = document.querySelector("#embedding-base-url");
const dataDir = document.querySelector("#data-dir");
const saveButton = document.querySelector("#save-button");
const message = document.querySelector("#message");
const setupBadge = document.querySelector("#setup-badge");
const apiKeyState = document.querySelector("#api-key-state");
const dataDirHelp = document.querySelector("#data-dir-help");
const configLocation = document.querySelector("#config-location");
const guideTitle = document.querySelector("#guide-title");

loadConfig();

document.querySelector("#toggle-secret").addEventListener("click", (event) => {
  const visible = apiKey.type === "text";
  apiKey.type = visible ? "password" : "text";
  event.currentTarget.textContent = visible ? "显示" : "隐藏";
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveButton.disabled = true;
  saveButton.textContent = "正在保存…";
  showMessage("", "");
  const payload = {
    base_url: baseUrl.value.trim(),
    model_name: modelName.value.trim(),
    embedding_model_name: embeddingModelName.value.trim(),
    embedding_base_url: embeddingBaseUrl.value.trim(),
    data_dir: dataDir.value.trim(),
  };
  if (apiKey.value.trim()) payload.api_key = apiKey.value.trim();

  try {
    const response = await fetch("/api/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(readError(result));
    applyConfig(result);
    apiKey.value = "";
    showMessage("配置已保存。请重启 NoviceSynapse 服务，让模型和数据目录配置生效。", "success");
  } catch (error) {
    showMessage(error.message || "保存失败，请检查配置。", "error");
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "保存配置";
  }
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(readError(result));
    applyConfig(result);
  } catch (error) {
    setupBadge.textContent = "读取失败";
    showMessage(error.message || "无法读取配置。", "error");
  }
}

function applyConfig(config) {
  baseUrl.value = config.client.base_url || "";
  modelName.value = config.client.model_name || "";
  embeddingModelName.value = config.embedding?.model_name || "qwen3-embedding";
  embeddingBaseUrl.value = config.embedding?.base_url || "";
  dataDir.value = config.storage.data_dir || "~/.novicesynapse";
  apiKeyState.textContent = config.client.api_key_configured
    ? "API Key 已配置；留空保存会保留原密钥。"
    : "API Key 尚未配置，请输入后保存。";
  setupBadge.textContent = config.setup_complete ? "已配置" : "首次配置";
  guideTitle.textContent = config.setup_complete ? "随时调整配置" : "三步完成配置";
  configLocation.textContent = `配置文件：${config.config_file}`;
  if (config.storage.environment_override) {
    dataDirHelp.textContent = `环境变量当前覆盖此项，实际目录为：${config.storage.effective_data_dir}`;
  } else {
    dataDirHelp.textContent = `重启后实际目录：${config.storage.effective_data_dir}`;
  }
}

function showMessage(text, type) {
  message.hidden = !text;
  message.textContent = text;
  message.className = `message ${type}`.trim();
}

function readError(result) {
  if (Array.isArray(result.detail)) {
    return result.detail.map((item) => item.msg).join("；");
  }
  return result.detail || "请求失败。";
}
