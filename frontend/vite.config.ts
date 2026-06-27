import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [react()],
  build: {
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes("node_modules")) return undefined;
          if (
            id.includes("/node_modules/react/") ||
            id.includes("/node_modules/react-dom/") ||
            id.includes("/node_modules/@tanstack/")
          ) {
            return "react-vendor";
          }
          if (id.includes("lightweight-charts")) return "chart-vendor";
          if (
            id.includes("@ant-design/") ||
            id.includes("/antd/") ||
            id.includes("/rc-")
          ) {
            return "antd-vendor";
          }
          return undefined;
        }
      }
    }
  },
  server: {
    port: 5173,
    allowedHosts: ["auto-poly.yoozo.xyz"],
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
        ws: true,
        configure(proxy) {
          proxy.on("error", (error: NodeJS.ErrnoException) => {
            if (error.code === "EPIPE" || error.code === "ECONNRESET") return;
            console.warn("[vite proxy] upstream error", error);
          });
        }
      }
    }
  }
});
