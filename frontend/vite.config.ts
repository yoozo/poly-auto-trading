import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      "/health": "http://localhost:8000",
      "/status": "http://localhost:8000",
      "/markets": "http://localhost:8000",
      "/signals": "http://localhost:8000",
      "/candles": "http://localhost:8000",
      "/indicators": "http://localhost:8000",
      "/orderbook": "http://localhost:8000",
      "/orders": "http://localhost:8000",
      "/notifications": "http://localhost:8000",
      "/stats": "http://localhost:8000"
    }
  }
});

