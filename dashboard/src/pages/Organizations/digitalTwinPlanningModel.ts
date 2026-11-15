import type { TFunction } from "i18next";
import type { DigitalWorkerPlan } from "../../api/modules/organizations";

export interface DigitalTwinPlanningIntake {
  sources: string;
  deliverable: string;
  reviewer: string;
  cadence?: string;
}

/** A deterministic, editable template. This does not authorize or execute work. */
export function buildDigitalTwinPlanDraft(
  goalTitle: string,
  intake: DigitalTwinPlanningIntake,
  t: TFunction,
): Pick<DigitalWorkerPlan, "title" | "approach" | "steps"> {
  const sources = intake.sources.trim();
  const deliverable = intake.deliverable.trim();
  const reviewer = intake.reviewer.trim();
  return {
    title: goalTitle,
    approach: [
      t("digitalTwinPlanning.approachSources", { sources }),
      t("digitalTwinPlanning.approachDeliverable", { deliverable }),
      t("digitalTwinPlanning.approachReviewer", { reviewer }),
      t("digitalTwinPlanning.approachCadence", {
        cadence:
          intake.cadence?.trim() || t("digitalTwinPlanning.notScheduled"),
      }),
      t("digitalTwinPlanning.approachBoundary"),
    ].join("\n"),
    steps: ["scope", "collect", "synthesize", "deliver", "review"].map(
      (step) => ({
        action: t(`digitalTwinPlanning.steps.${step}.action`),
        expected_output: t(`digitalTwinPlanning.steps.${step}.output`, {
          deliverable,
          reviewer,
        }),
      }),
    ),
  };
}
