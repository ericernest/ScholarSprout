const form = document.querySelector("#settings-form");
const baseUrl = document.querySelector("#base-url");
const apiKey = document.querySelector("#api-key");
const modelName = document.querySelector("#model-name");
const embeddingModelName = document.querySelector("#embedding-model-name");
const embeddingBaseUrl = document.querySelector("#embedding-base-url");
const embeddingApiKey = document.querySelector("#embedding-api-key");
const mineruBaseUrl = document.querySelector("#mineru-base-url");
const mineruApiKey = document.querySelector("#mineru-api-key");
const dataDir = document.querySelector("#data-dir");
const saveButton = document.querySelector("#save-button");
const message = document.querySelector("#message");
const setupBadge = document.querySelector("#setup-badge");
const apiKeyState = document.querySelector("#api-key-state");
const dataDirHelp = document.querySelector("#data-dir-help");
const embeddingApiKeyState = document.querySelector("#embedding-api-key-state");
const mineruApiKeyState = document.querySelector("#mineru-api-key-state");
const guideTitle = document.querySelector("#guide-title");

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
    mineru_base_url: mineruBaseUrl.value.trim(),
    clear_mineru_api_key: !mineruApiKey.value.trim(),
    data_dir: dataDir.value.trim(),
  };
  if (apiKey.value.trim()) payload.api_key = apiKey.value.trim();
  if (embeddingApiKey.value.trim()) payload.embedding_api_key = embeddingApiKey.value.trim();
  if (mineruApiKey.value.trim()) payload.mineru_api_key = mineruApiKey.value.trim();

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
    mineruApiKey.value = "";
    showMessage(
      result.restart_required
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
  setValue(mineruBaseUrl, config.mineru?.base_url || "");
  setValue(dataDir, config.storage?.data_dir || "~/.novicesynapse");
  setText(apiKeyState, config.client?.api_key_configured
    ? "API Key 已配置；留空保存会保留原密钥。"
    : "API Key 尚未配置，请输入后保存。");
  setText(embeddingApiKeyState, config.embedding?.uses_client_api_key
    ? "当前复用基础模型 API Key；输入后可改用独立 Key。"
    : "已配置独立 Key；留空保存会改为复用基础模型 API Key。");
  setText(mineruApiKeyState, config.mineru?.api_key_configured
    ? "MinerU API Key 已配置；留空保存会关闭 MinerU。"
    : "尚未配置；MinerU 当前不启用。");
  setText(setupBadge, config.setup_complete ? "已配置" : "首次配置");
  setText(guideTitle, config.setup_complete ? "模型数据配置" : "三步完成配置");
  if (config.storage?.environment_override) {
    setText(dataDirHelp, `环境变量当前覆盖此项，实际目录为：${config.storage.effective_data_dir}`);
  } else {
    setText(dataDirHelp, `当前数据目录：${config.storage?.effective_data_dir || ""}；修改后需重启。`);
  }
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
