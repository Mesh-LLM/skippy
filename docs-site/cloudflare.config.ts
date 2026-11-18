import { defineConfig, defineWorker } from "cf/config";

export default defineConfig({
  accountId: "d9b901b29c44e0c48163aace3aa90cfd",
  worker: defineWorker({
    name: "skippy-docs",
    compatibilityDate: "2026-11-18",
    assets: {
      htmlHandling: "force-trailing-slash",
      notFoundHandling: "404-page",
    },
    domains: ["skippy.sh"],
  }),
});
