const modeMap = {
  chat: {
    label: "日常聊天",
    endpoint: "/chat",
  },
  paper_reading: {
    label: "论文精读",
    endpoint: "/paper_reading",
  },
  domain_onboarding: {
    label: "方向入门",
    endpoint: "/domain_onboarding",
  },
};

let currentMode = "chat";
const sessionId = getSessionId();

const homePage = document.querySelector("#home-page");
const chatPage = document.querySelector("#chat-page");
const messages = document.querySelector("#messages");
const form = document.querySelector("#chat-form");
const input = document.querySelector("#message-input");
const modeButton = document.querySelector("#mode-button");
const modeMenu = document.querySelector("#mode-menu");
const modePill = document.querySelector("#mode-pill");
const sendButton = document.querySelector("#send-button");
const cursorGlow = document.querySelector("#cursor-glow");

showPage();
bindChatPage();
startParticleField();
bindCursorGlow();

// Show home or chat page by current path.
function showPage() {
  const isChatPage = window.location.pathname === "/app";
  homePage.hidden = isChatPage;
  chatPage.hidden = !isChatPage;
}

// Bind chat page interactions.
function bindChatPage() {
  if (!form) {
    return;
  }

  modeButton.addEventListener("click", () => {
    modeMenu.hidden = !modeMenu.hidden;
  });

  modeMenu.addEventListener("click", (event) => {
    const mode = event.target.dataset.mode;
    if (!mode) {
      return;
    }

    currentMode = mode;
    modePill.textContent = `当前模式：${modeMap[currentMode].label}`;
    modeMenu.hidden = true;
    input.focus();
  });

  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = `${Math.min(input.scrollHeight, 180)}px`;
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    await sendMessage();
  });
}

// Send user message to current backend endpoint.
async function sendMessage() {
  const content = input.value.trim();
  if (!content) {
    return;
  }

  appendMessage("user", content);
  input.value = "";
  input.style.height = "auto";
  setLoading(true);

  try {
    const response = await fetch(modeMap[currentMode].endpoint, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        session_id: sessionId,
        content,
        user_id: "local-web",
        metadata: {},
      }),
    });

    if (!response.ok) {
      throw new Error(`请求失败：${response.status}`);
    }

    const data = await response.json();
    appendMessage("assistant", extractReply(data));
  } catch (error) {
    appendMessage("assistant", `请求失败：${error.message}`);
  } finally {
    setLoading(false);
  }
}

// Extract assistant text from common response shapes.
function extractReply(response) {
  const value =
    response?.content ??
    response?.text ??
    response?.message ??
    response?.data?.content ??
    "后端没有返回内容。";

  if (typeof value === "string") {
    return value;
  }

  if (typeof value?.text === "string") {
    return value.text;
  }

  return JSON.stringify(value, null, 2);
}

// Append one visible message.
function appendMessage(role, content) {
  const item = document.createElement("article");
  item.className = `message ${role}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  bubble.textContent = content;

  item.appendChild(bubble);
  messages.appendChild(item);
  messages.scrollTop = messages.scrollHeight;
}

// Toggle request state.
function setLoading(isLoading) {
  sendButton.disabled = isLoading;
  sendButton.textContent = isLoading ? "发送中" : "发送";
}

// Get persistent local session id.
function getSessionId() {
  const key = "novicesynapse_session_id";
  const existing = window.localStorage.getItem(key);
  if (existing) {
    return existing;
  }

  const created = `web-${crypto.randomUUID()}`;
  window.localStorage.setItem(key, created);
  return created;
}

// Bind cursor glow position.
function bindCursorGlow() {
  let targetX = window.innerWidth / 2;
  let targetY = window.innerHeight / 2;
  let currentX = targetX;
  let currentY = targetY;

  window.addEventListener("pointermove", (event) => {
    targetX = event.clientX;
    targetY = event.clientY;
  });

  function tick() {
    currentX += (targetX - currentX) * 0.12;
    currentY += (targetY - currentY) * 0.12;
    cursorGlow.style.transform = `translate(${currentX}px, ${currentY}px)`;
    requestAnimationFrame(tick);
  }

  tick();
}

// Start lightweight canvas particles.
function startParticleField() {
  const canvas = document.querySelector("#particle-canvas");
  const ctx = canvas.getContext("2d");
  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const pointer = {
    x: window.innerWidth / 2,
    y: window.innerHeight / 2,
  };

  let width = 0;
  let height = 0;
  let particles = [];

  window.addEventListener("pointermove", (event) => {
    pointer.x = event.clientX;
    pointer.y = event.clientY;
  });

  window.addEventListener("resize", resize);
  resize();

  if (reducedMotion) {
    drawStaticParticles();
    return;
  }

  requestAnimationFrame(draw);

  // Resize canvas and reduce particle count on small screens.
  function resize() {
    width = window.innerWidth;
    height = window.innerHeight;
    canvas.width = Math.floor(width * window.devicePixelRatio);
    canvas.height = Math.floor(height * window.devicePixelRatio);
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
    ctx.setTransform(window.devicePixelRatio, 0, 0, window.devicePixelRatio, 0, 0);

    const baseCount = width < 720 ? 48 : 88;
    const hardwareLimit = navigator.hardwareConcurrency && navigator.hardwareConcurrency <= 4 ? 0.68 : 1;
    const count = Math.floor(baseCount * hardwareLimit);
    particles = Array.from({ length: count }, createParticle);
  }

  // Create one particle.
  function createParticle() {
    return {
      x: Math.random() * width,
      y: Math.random() * height,
      vx: (Math.random() - 0.5) * 0.34,
      vy: (Math.random() - 0.5) * 0.34,
      r: Math.random() * 1.8 + 0.6,
    };
  }

  // Draw one animation frame.
  function draw() {
    ctx.clearRect(0, 0, width, height);
    drawConnections();
    drawParticles();
    requestAnimationFrame(draw);
  }

  // Draw particle links.
  function drawConnections() {
    for (let i = 0; i < particles.length; i += 1) {
      for (let j = i + 1; j < particles.length; j += 1) {
        const a = particles[i];
        const b = particles[j];
        const distance = Math.hypot(a.x - b.x, a.y - b.y);
        if (distance > 132) {
          continue;
        }

        ctx.strokeStyle = `rgba(102, 245, 214, ${0.13 * (1 - distance / 132)})`;
        ctx.lineWidth = 1;
        ctx.beginPath();
        ctx.moveTo(a.x, a.y);
        ctx.lineTo(b.x, b.y);
        ctx.stroke();
      }
    }
  }

  // Draw and update particles.
  function drawParticles() {
    for (const particle of particles) {
      const dx = pointer.x - particle.x;
      const dy = pointer.y - particle.y;
      const distance = Math.hypot(dx, dy);
      if (distance < 180) {
        particle.vx += dx * 0.000018;
        particle.vy += dy * 0.000018;
      }

      particle.x += particle.vx;
      particle.y += particle.vy;
      particle.vx *= 0.995;
      particle.vy *= 0.995;

      if (particle.x < -20) particle.x = width + 20;
      if (particle.x > width + 20) particle.x = -20;
      if (particle.y < -20) particle.y = height + 20;
      if (particle.y > height + 20) particle.y = -20;

      ctx.fillStyle = "rgba(190, 255, 240, 0.78)";
      ctx.beginPath();
      ctx.arc(particle.x, particle.y, particle.r, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  // Draw one static frame when reduced motion is requested.
  function drawStaticParticles() {
    ctx.clearRect(0, 0, width, height);
    drawConnections();
    drawParticles();
  }
}
