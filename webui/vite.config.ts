import { fileURLToPath, URL } from "node:url";
import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

const page = (name: string) => fileURLToPath(new URL(`./pages/${name}.html`, import.meta.url));

export default defineConfig({
  base: "/static/app-v2/",
  plugins: [vue()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8000",
      "/chat": "http://127.0.0.1:8000",
      "/paper_reading": "http://127.0.0.1:8000",
      "/domain_onboarding": "http://127.0.0.1:8000",
      "/static": "http://127.0.0.1:8000"
    }
  },
  build: {
    outDir: "../gateway/static/app-v2",
    emptyOutDir: true,
    sourcemap: false,
    rollupOptions: {
      input: {
        home: fileURLToPath(new URL("./index.html", import.meta.url)),
        chat: page("chat"),
        "paper-reading": page("paper-reading"),
        "domain-onboarding": page("domain-onboarding"),
        library: page("library"),
        settings: page("settings"),
        tutorial: page("tutorial")
      }
    }
  }
});
