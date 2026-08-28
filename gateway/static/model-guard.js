(() => {
  let cachedConfig = null;
  let cachedAt = 0;

  function installStyles() {
    if (document.querySelector("#model-guard-styles")) return;
    const style = document.createElement("style");
    style.id = "model-guard-styles";
    style.textContent = `
      .model-guard-overlay{position:fixed;inset:0;z-index:20000;display:grid;place-items:center;padding:18px;background:rgba(3,14,18,.72);backdrop-filter:blur(6px)}
      .model-guard-dialog{display:grid;gap:14px;width:min(430px,100%);padding:24px;border:1px solid rgba(102,245,214,.3);border-radius:22px;color:#eafff7;background:linear-gradient(150deg,rgba(7,34,31,.99),rgba(18,27,54,.99));box-shadow:0 30px 90px rgba(0,0,0,.52);font-family:Inter,"Microsoft YaHei",sans-serif}
      .model-guard-heading{display:grid;grid-template-columns:44px 1fr;align-items:center;gap:12px}.model-guard-heading img{width:44px;height:44px}.model-guard-heading small{display:block;margin-bottom:4px;color:#66f5d6;font-size:.68rem;font-weight:850;letter-spacing:.1em}.model-guard-heading h2{margin:0;color:#f1fffb;font-size:1.25rem}
      .model-guard-dialog p{margin:0;color:#b9d8d1;font-size:.86rem;line-height:1.65}.model-guard-actions{display:flex;justify-content:flex-end;gap:8px;margin-top:4px}.model-guard-actions button,.model-guard-actions a{border-radius:99px;padding:10px 15px;font:750 .82rem/1 Inter,"Microsoft YaHei",sans-serif;text-decoration:none;cursor:pointer}.model-guard-cancel{border:1px solid rgba(255,255,255,.18);color:#d9eee9;background:transparent}.model-guard-settings{border:0;color:#04110e;background:linear-gradient(135deg,#66f5d6,#b8fff1)}
    `;
    document.head.append(style);
  }

  function showPrompt(message = "基础模型尚未配置。完成配置后即可使用生成、解释与分析功能。") {
    installStyles();
    const existing = document.querySelector(".model-guard-overlay");
    if (existing) {
      existing.querySelector("a")?.focus();
      return;
    }
    const overlay = document.createElement("div");
    overlay.className = "model-guard-overlay";
    overlay.innerHTML = `
      <section class="model-guard-dialog" role="dialog" aria-modal="true" aria-labelledby="model-guard-title">
        <div class="model-guard-heading"><img src="/static/favicon.svg" alt=""><div><small>LOCAL MODEL</small><h2 id="model-guard-title">先完成模型配置</h2></div></div>
        <p>${message}</p>
        <div class="model-guard-actions"><button class="model-guard-cancel" type="button">暂不配置</button><a class="model-guard-settings" href="/settings">前往配置</a></div>
      </section>
    `;
    overlay.querySelector(".model-guard-cancel")?.addEventListener("click", () => overlay.remove());
    overlay.addEventListener("click", (event) => {
      if (event.target === overlay) overlay.remove();
    });
    document.body.append(overlay);
    overlay.querySelector("a")?.focus();
  }

  async function ensureBaseModelConfigured() {
    try {
      if (!cachedConfig || Date.now() - cachedAt > 10000) {
        const response = await fetch("/api/config", { cache: "no-store" });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        cachedConfig = await response.json();
        cachedAt = Date.now();
      }
      if (cachedConfig?.setup_complete) return true;
      showPrompt();
      return false;
    } catch {
      showPrompt("暂时无法确认基础模型配置。请进入配置页检查 Base URL、API Key 与模型名称。");
      return false;
    }
  }

  window.ensureBaseModelConfigured = ensureBaseModelConfigured;
  window.invalidateBaseModelConfig = () => {
    cachedConfig = null;
    cachedAt = 0;
  };
})();
