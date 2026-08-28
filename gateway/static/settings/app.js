const form = document.querySelector("#settings-form");
const baseUrl = document.querySelector("#base-url");
const apiKey = document.querySelector("#api-key");
const modelName = document.querySelector("#model-name");
const embeddingModelName = document.querySelector("#embedding-model-name");
const embeddingBaseUrl = document.querySelector("#embedding-base-url");
const embeddingApiKey = document.querySelector("#embedding-api-key");
const dataDir = document.querySelector("#data-dir");
const saveButton = document.querySelector("#save-button");
const message = document.querySelector("#message");
const setupBadge = document.querySelector("#setup-badge");
const apiKeyState = document.querySelector("#api-key-state");
const dataDirHelp = document.querySelector("#data-dir-help");
const embeddingApiKeyState = document.querySelector("#embedding-api-key-state");
const guideTitle = document.querySelector("#guide-title");
const feishuEnabled = document.querySelector("#feishu-enabled");
const feishuAppId = document.querySelector("#feishu-app-id");
const feishuAppSecret = document.querySelector("#feishu-app-secret");
const clearFeishuAppSecret = document.querySelector("#clear-feishu-app-secret");
const feishuSecretState = document.querySelector("#feishu-secret-state");
const feishuConfigState = document.querySelector("#feishu-config-state");

document.querySelectorAll("[data-config-tab]").forEach((tab) => {
  tab.addEventListener("click", () => selectConfigTab(tab.dataset.configTab));
});

if (form) loadConfig();

const primarySecretToggle = document.querySelector("#toggle-secret");
if (primarySecretToggle) primarySecretToggle.dataset.secretTarget = "api-key";
document.querySelectorAll("#toggle-secret, .toggle-secret").forEach((button) => {
  button.addEventListener("click", (event) => {
    const input = document.getElementById(event.currentTarget.dataset.secretTarget);
    if (!input) return;
    const visible = input.type === "text";
    input.type = visible ? "password" : "text";
    event.currentTarget.textContent = visible ? "显示" : "隐藏";
  });
});

form?.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveButton) saveButton.disabled = true;
  setText(saveButton, "正在保存…");
  showMessage("", "");
  const payload = {
    base_url: baseUrl.value.trim(),
    model_name: modelName.value.trim(),
    embedding_model_name: embeddingModelName.value.trim(),
    embedding_base_url: embeddingBaseUrl.value.trim(),
    clear_embedding_api_key: !embeddingApiKey.value.trim(),
    data_dir: dataDir.value.trim(),
    feishu_enabled: Boolean(feishuEnabled?.checked),
    feishu_app_id: feishuAppId?.value.trim() || "",
    clear_feishu_app_secret: Boolean(clearFeishuAppSecret?.checked),
  };
  if (apiKey.value.trim()) payload.api_key = apiKey.value.trim();
  if (embeddingApiKey.value.trim()) payload.embedding_api_key = embeddingApiKey.value.trim();
  if (feishuAppSecret?.value.trim()) payload.feishu_app_secret = feishuAppSecret.value.trim();

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
    embeddingApiKey.value = "";
    if (feishuAppSecret) feishuAppSecret.value = "";
    if (clearFeishuAppSecret) clearFeishuAppSecret.checked = false;
    showMessage(
      result.channels_restart_required
        ? "飞书配置已保存；请重启研见后建立或关闭飞书连接。"
        : result.restart_required
        ? "配置已保存；数据目录或运行时未能热更新，请重启服务后生效。"
        : "配置已保存并立即生效，新请求会使用当前配置。",
      "success",
    );
  } catch (error) {
    showMessage(error.message || "保存失败，请检查配置。", "error");
  } finally {
    if (saveButton) saveButton.disabled = false;
    setText(saveButton, "保存配置");
  }
});

async function loadConfig() {
  try {
    const response = await fetch("/api/config", { cache: "no-store" });
    const result = await response.json();
    if (!response.ok) throw new Error(readError(result));
    applyConfig(result);
  } catch (error) {
    setText(setupBadge, "读取失败");
    showMessage(error.message || "无法读取配置。", "error");
  }
}

function applyConfig(config) {
  setValue(baseUrl, config.client?.base_url || "");
  setValue(modelName, config.client?.model_name || "");
  setValue(embeddingModelName, config.embedding?.model_name || "qwen3-embedding");
  setValue(embeddingBaseUrl, config.embedding?.base_url || "");
  setValue(dataDir, config.storage?.data_dir || "~/.novicesynapse");
  if (feishuEnabled) feishuEnabled.checked = Boolean(config.channels?.feishu?.enabled);
  setValue(feishuAppId, config.channels?.feishu?.app_id || "");
  setText(apiKeyState, config.client?.api_key_configured
    ? "已保存在本地后端；留空可保留。"
    : "尚未保存 API Key。");
  setText(embeddingApiKeyState, config.embedding?.uses_client_api_key
    ? "当前复用基础模型 API Key。"
    : "独立 Key 已保存在本地后端。");
  setText(feishuSecretState, config.channels?.feishu?.app_secret_configured
    ? "已保存在本地后端；留空可保留。"
    : "尚未保存 App Secret。");
  setText(feishuConfigState, config.channels?.feishu?.environment_override
    ? "环境变量正在覆盖页面中的飞书凭据；修改页面配置后仍需移除环境变量并重启。"
    : config.channels?.feishu?.enabled
      ? "飞书已启用；如刚修改凭据，请重启研见后生效。"
      : "飞书未启用。保存凭据并打开开关后，重启研见即可连接。");
  setText(setupBadge, config.setup_complete ? "已配置" : "首次配置");
  setText(guideTitle, config.setup_complete ? "模型数据配置" : "三步完成配置");
  if (config.storage?.environment_override) {
    setText(dataDirHelp, `环境变量当前覆盖此项，实际目录为：${config.storage.effective_data_dir}`);
  } else {
    setText(dataDirHelp, `当前数据目录：${config.storage?.effective_data_dir || ""}；修改后需重启。`);
  }
}

function selectConfigTab(name) {
  document.querySelectorAll("[data-config-tab]").forEach((tab) => {
    const active = tab.dataset.configTab === name;
    tab.classList.toggle("is-active", active);
    tab.setAttribute("aria-selected", String(active));
  });
  document.querySelectorAll("[data-config-panel]").forEach((panel) => {
    panel.hidden = panel.dataset.configPanel !== name;
  });
}

function setValue(node, value) {
  if (node) node.value = value;
}

function setText(node, value) {
  if (node) node.textContent = value;
}

function showMessage(text, type) {
  if (!message) return;
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
