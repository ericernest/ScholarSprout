<script setup lang="ts">
import { nextTick, onBeforeUnmount, onMounted, ref } from "vue";
import type { SurfaceDefinition } from "../config/surfaces";

const props = defineProps<{ surface: SurfaceDefinition }>();
const dialog = ref<HTMLDialogElement | null>(null);
const storageKey = "seefurther_product_guide_v1";
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
    <div class="pa-guide-header">
      <div class="pa-guide-mark"><img :src="faviconPath" alt="" /></div>
      <div class="pa-guide-heading">
        <p class="pa-guide-kicker">研见 · SeeFurther</p>
        <h2>{{ surface.guideTitle }}</h2>
      </div>
    </div>
    <ol>
      <li v-for="(step, index) in surface.guideSteps" :key="step">
        <span>{{ index + 1 }}</span><p>{{ step }}</p>
      </li>
    </ol>
    <button class="pa-guide-primary" type="button" @click="closeGuide">开始使用</button>
  </dialog>
</template>
