import { createServer } from "vite";
import { fileURLToPath } from "node:url";

const server = await createServer({
  root: fileURLToPath(new URL(".", import.meta.url)),
  server: {
    host: "0.0.0.0",
    port: Number(process.env.PORT || 5180),
    strictPort: true,
  },
});
await server.listen();
server.printUrls();
