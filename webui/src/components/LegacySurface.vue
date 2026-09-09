<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from "vue";
import ProductGuide from "./ProductGuide.vue";
import { surfaces, type SurfaceId } from "../config/surfaces";

const props = defineProps<{ surfaceId: SurfaceId }>();
const surface = computed(() => surfaces[props.surfaceId]);
const faviconPath = "/static/app-v2/favicon.svg";
const host = ref<HTMLElement | null>(null);
const status = ref<"loading" | "ready" | "error">("loading");
const errorMessage = ref("");
const injectedNodes: HTMLElement[] = [];
const originalBodyClass = document.body.className;

function absoluteResource(value: string): string {
  return new URL(value, `${window.location.origin}${surface.value.sourcePath}`).href;
}

async function appendStyles(source: Document): Promise<void> {
  const links = Array.from(source.querySelectorAll<HTMLLinkElement>('link[rel="stylesheet"][href]'));
  await Promise.all(links.map((sourceLink) => new Promise<void>((resolve, reject) => {
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = absoluteResource(sourceLink.getAttribute("href") || "");
    link.dataset.scholarsproutLegacy = props.surfaceId;
    link.onload = () => resolve();
    link.onerror = () => reject(new Error(`样式加载失败：${link.href}`));
    document.head.append(link);
    injectedNodes.push(link);
  })));

  const override = document.createElement("link");
  override.rel = "stylesheet";
  override.href = "/static/app-v2/styles/legacy-overrides.css";
  override.dataset.scholarsproutLegacy = `${props.surfaceId}-overrides`;
  document.head.append(override);
  injectedNodes.push(override);
}

async function appendScripts(source: Document): Promise<void> {
  const scripts = Array.from(source.querySelectorAll<HTMLScriptElement>("body script[src]"));
  for (const sourceScript of scripts) {
    await new Promise<void>((resolve, reject) => {
      const script = document.createElement("script");
      script.src = absoluteResource(sourceScript.getAttribute("src") || "");
      script.dataset.scholarsproutLegacy = props.surfaceId;
      if (sourceScript.type) script.type = sourceScript.type;
      script.onload = () => resolve();
      script.onerror = () => reject(new Error(`脚本加载失败：${script.src}`));
      document.body.append(script);
      injectedNodes.push(script);
    });
  }
}

async function loadSurface(): Promise<void> {
  try {
    const response = await fetch(surface.value.sourcePath, { cache: "no-cache" });
    if (!response.ok) throw new Error(`旧版功能页面不可用（HTTP ${response.status}）`);
    const source = new DOMParser().parseFromString(await response.text(), "text/html");
    const target = host.value;
    if (!target) throw new Error("页面挂载点未就绪");

    document.title = surface.value.title;
    document.body.className = source.body.className;
    const content = Array.from(source.body.childNodes).filter(
      (node) => !(node instanceof HTMLScriptElement)
    );
    target.replaceChildren(...content);
    await appendStyles(source);
    await appendScripts(source);
    document.documentElement.dataset.scholarsproutReady = props.surfaceId;
    status.value = "ready";
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : "页面加载失败";
    status.value = "error";
  }
}

onMounted(loadSurface);
onBeforeUnmount(() => {
  injectedNodes.forEach((node) => node.remove());
  document.body.className = originalBodyClass;
  delete document.documentElement.dataset.scholarsproutReady;
});
</script>

<template>
  <main class="pa-surface-root" :data-surface="surfaceId">
    <div v-if="status === 'loading'" class="pa-boot" role="status" aria-live="polite">
      <img :src="faviconPath" alt="" width="58" height="58" />
      <strong>科研萌芽·ScholarSprout</strong>
      <span>正在点亮研究工作台…</span>
    </div>
    <div v-if="status === 'error'" class="pa-boot pa-boot-error" role="alert">
      <strong>页面暂时无法载入</strong>
      <span>{{ errorMessage }}</span>
      <button type="button" @click="loadSurface">重新加载</button>
    </div>
    <div ref="host" class="pa-legacy-host" :aria-busy="status === 'loading'" />
    <ProductGuide v-if="status === 'ready'" :surface="surface" />
  </main>
</template>
