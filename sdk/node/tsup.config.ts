import { defineConfig } from "tsup";

export default defineConfig({
  entry: ["src/index.ts"],
  format: ["esm", "cjs"],
  dts: true,
  clean: true,
  sourcemap: true,
  target: "node22",
  platform: "node",
  // Express is a peer dependency: targets bring their own copy and the SDK only
  // ever touches the request/response objects it is handed.
  external: ["express"],
});
