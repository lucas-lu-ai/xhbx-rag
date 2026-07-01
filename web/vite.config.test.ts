import type { UserConfig } from "vite";
import config from "./vite.config";

function getUserConfig(): UserConfig {
  if (typeof config === "function") {
    throw new TypeError("vite.config.ts should export an object config");
  }
  return config as UserConfig;
}

test("allows ngrok free domains for public dev tunnels", () => {
  const userConfig = getUserConfig();

  expect(userConfig.server?.allowedHosts).toEqual(expect.arrayContaining([".ngrok-free.app"]));
});
