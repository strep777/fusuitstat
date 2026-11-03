import { fileURLToPath, URL } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import vue from "@vitejs/plugin-vue";
import { defineConfig, type PluginOption } from "vite";

const apiTarget = process.env.VITE_DEV_API_TARGET ?? "http://127.0.0.1:8000";

/**
 * 本地开发把 `/admin/*` 交给后台入口，避免刷新后台子路由时落回前台 404。
 * 生产环境由 Nginx 做同样的 rewrite（见 文档/14）。
 */
function adminEntryFallback(): PluginOption {
  const rewrite = (req: { url?: string }, _res: unknown, next: () => void): void => {
    if (req.url && req.url.startsWith("/admin") && !req.url.startsWith("/admin.html")) {
      req.url = "/admin.html";
    }
    next();
  };
  return {
    name: "starium-admin-entry-fallback",
    configureServer(server) {
      server.middlewares.use(rewrite);
    },
    configurePreviewServer(server) {
      server.middlewares.use(rewrite);
    },
  };
}

export default defineConfig({
  plugins: [vue(), tailwindcss() as PluginOption, adminEntryFallback()],
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
