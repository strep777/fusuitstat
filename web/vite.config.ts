import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig, type PluginOption } from "vite";

const apiTarget = process.env.VITE_DEV_API_TARGET ?? "http://127.0.0.1:8000";

export default defineConfig({
  plugins: [vue(), tailwindcss() as PluginOption],
  resolve: {
    alias: {
      "@": fileURLToPath(new URL("./src", import.meta.url)),
    },
  },
  build: {
    target: "es2022",
    cssTarget: "chrome100",
    sourcemap: false,
    assetsInlineLimit: 2048,
    rollupOptions: {
      input: {
        // 前台与后台两个独立构建入口（04 文档：Vue 前台与后台两个构建入口）
        site: fileURLToPath(new URL("./index.html", import.meta.url)),
        admin: fileURLToPath(new URL("./admin.html", import.meta.url)),
      },
      output: {
        manualChunks: {
          vendor: ["vue", "vue-router", "pinia"],
        },
      },
    },
  },
  server: {
    host: "127.0.0.1",
    port: 5173,
    strictPort: false,
    proxy: {
      "/api": {
        target: apiTarget,
        changeOrigin: false,
      },
    },
  },
  preview: {
    host: "127.0.0.1",
    port: 4173,
  },
});
