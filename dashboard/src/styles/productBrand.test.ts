import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import en from "../locales/en.json";
import zh from "../locales/zh.json";

const dashboard = resolve(__dirname, "../..");

describe("AI-TAgent product branding", () => {
  it("uses the product names in browser and installed app metadata", () => {
    const html = readFileSync(resolve(dashboard, "index.html"), "utf8");
    const manifest = JSON.parse(
      readFileSync(resolve(dashboard, "public/manifest.json"), "utf8"),
    );
    expect(html).toContain("<title>AI-TAgent · 轻智能体系统</title>");
    expect(manifest.name).toBe("AI-TAgent");
    expect(manifest.short_name).toBe("AI-TAgent");
    expect(zh.app.pageTitle).toContain("轻智能体系统");
    expect(en.app.pageTitle).toContain("AI-TAgent");
  });

  it("preserves compatible commands and third-party connector names", () => {
    expect(en.channels.label_octopbot).toBe("OctopBot");
    expect(zh.setupWizard.browser.manualCommand).toBe("octop install-browsers");
  });

  it("ships readable wordmarks for both themes", () => {
    for (const theme of ["light", "dark"]) {
      const svg = readFileSync(
        resolve(dashboard, `public/ai-tagent-wordmark-${theme}.svg`),
        "utf8",
      );
      expect(svg).toContain("AI-TAgent");
      expect(svg).toContain("轻智能体系统");
    }
  });
});
