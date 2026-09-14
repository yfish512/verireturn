import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/ops": "http://127.0.0.1:8000",
      "/tools": "http://127.0.0.1:8000",
      "/agent/threads": "http://127.0.0.1:8000",
      "/agent/confirmations": "http://127.0.0.1:8000",
      "/agent/runs": "http://127.0.0.1:8000",
    },
  },
});
