<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { SurfaceDefinition } from "../config/surfaces";

const props = defineProps<{ surface: SurfaceDefinition }>();
const dialog = ref<HTMLDialogElement | null>(null);
const storageKey = "paperaurora_product_guide_v1";
const faviconPath = "/static/app-v2/favicon.svg";
let openTimer = 0;

function openGuide(): void {
  if (!dialog.value?.open) dialog.value?.showModal();
}

function closeGuide(): void {
  dialog.value?.close();
  localStorage.setItem(storageKey, "seen");
}

onMounted(async () => {
  if (!props.surface.autoOpenGuide || localStorage.getItem(storageKey)) return;
  await nextTick();
  openTimer = window.setTimeout(openGuide, 550);
});
onBeforeUnmount(() => window.clearTimeout(openTimer));
</script>

<template>
  <button class="pa-help-button" type="button" aria-label="打开页面使用指南" @click="openGuide">?</button>
  <dialog ref="dialog" class="pa-guide-dialog" @cancel.prevent="closeGuide">
    <div class="pa-guide-mark"><img :src="faviconPath" alt="" /></div>
    <p class="pa-guide-kicker">研见 · PAPER AURORA</p>
    <h2>{{ surface.guideTitle }}</h2>
    <ol>
      <li v-for="(step, index) in surface.guideSteps" :key="step">
        <span>{{ index + 1 }}</span><p>{{ step }}</p>
      </li>
    </ol>
    <p class="pa-guide-note">所有数据与模型配置仍由当前 PaperAurora 后端处理；本地版不会额外启动前端服务。</p>
    <button class="pa-guide-primary" type="button" @click="closeGuide">开始使用</button>
  </dialog>
</template>
