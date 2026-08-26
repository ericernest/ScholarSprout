import { createApp } from "vue";
import LegacySurface from "../components/LegacySurface.vue";
import type { SurfaceId } from "../config/surfaces";
import "../styles/foundation.css";

export function mountSurface(surfaceId: SurfaceId): void {
  const root = document.querySelector<HTMLElement>("#app");
  if (!root) throw new Error(`SeeFurther: missing #app for ${surfaceId}`);
  createApp(LegacySurface, { surfaceId }).mount(root);
}
