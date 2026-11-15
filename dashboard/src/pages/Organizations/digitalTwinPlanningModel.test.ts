import { createInstance } from "i18next";
import { describe, expect, it } from "vitest";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";
import { buildDigitalTwinPlanDraft } from "./digitalTwinPlanningModel";

async function translator(language: string) {
  const i18n = createInstance();
  await i18n.init({
    lng: language,
    resources: { en: { translation: en }, zh: { translation: zh } },
    interpolation: { escapeValue: false },
  });
  return i18n.t.bind(i18n);
}

describe("digital twin planning template", () => {
  it.each(["zh", "en"])(
    "creates a localized draft without workflow state (%s)",
    async (language) => {
      const draft = buildDigitalTwinPlanDraft(
        "Market report",
        {
          sources: "  https://example.com & configured connector  ",
          deliverable: "  Weekly evidence report  ",
          reviewer: "  Research lead  ",
          cadence: "  Mondays  ",
        },
        await translator(language),
      );
      expect(draft.title).toBe("Market report");
      expect(draft.approach).toContain(
        "https://example.com & configured connector",
      );
      expect(draft.approach).toContain("Mondays");
      expect(draft.steps).toHaveLength(5);
      expect(draft.steps[3].expected_output).toContain(
        "Weekly evidence report",
      );
      expect(draft.steps[4].expected_output).toContain("Research lead");
      expect(JSON.stringify(draft)).not.toContain("digitalTwinPlanning.");
      expect(Object.keys(draft)).toEqual(["title", "approach", "steps"]);
    },
  );

  it.each(["zh", "en"])(
    "respects plan API size limits at maximum intake lengths (%s)",
    async (language) => {
      const draft = buildDigitalTwinPlanDraft(
        "G".repeat(200),
        {
          sources: "S".repeat(1000),
          deliverable: "D".repeat(500),
          reviewer: "R".repeat(120),
          cadence: "C".repeat(200),
        },
        await translator(language),
      );
      expect(draft.title.length).toBeLessThanOrEqual(200);
      expect(draft.approach.length).toBeLessThanOrEqual(4000);
      for (const step of draft.steps) {
        expect(step.action.length).toBeLessThanOrEqual(500);
        expect(step.expected_output.length).toBeLessThanOrEqual(1000);
      }
    },
  );

  it("does not invent a schedule or automatic knowledge publication", async () => {
    const draft = buildDigitalTwinPlanDraft(
      "Goal",
      {
        sources: "Source",
        deliverable: "Report",
        reviewer: "Manager",
      },
      await translator("en"),
    );
    expect(draft.approach).toContain("no schedule created");
    expect(draft.approach).toContain("existing scene approval process");
    expect(draft.steps[4].expected_output).toContain(
      "requiring separate review",
    );
  });
});
