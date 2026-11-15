import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import ScenePlanGuide from "./ScenePlanGuide";
import type {
  DigitalWorker,
  EnterpriseScene,
} from "../../api/modules/organizations";
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (k: string) => k }),
}));
const scene = {
  scene_id: "s",
  organization_id: "o",
  position_id: "p",
  agent_id: "a",
  enabled: true,
  name: "Scene",
} as EnterpriseScene;
const worker = {
  worker_id: "w",
  organization_id: "o",
  position_id: "p",
  agent_id: "a",
  status: "active",
  name: "Worker",
} as DigitalWorker;
afterEach(cleanup);
it("does not submit anything and requires explicit matching worker selection", async () => {
  const select = vi.fn();
  render(
    <ScenePlanGuide
      scene={scene}
      workers={[worker]}
      canManage
      onSelect={select}
      onClose={() => {}}
    />,
  );
  expect(select).not.toHaveBeenCalled();
  expect(
    screen.getByRole("button", {
      name: "organizations.scenePlanGuideContinue",
    }),
  ).toBeDisabled();
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByText("Worker"));
  fireEvent.click(
    screen.getByRole("button", {
      name: "organizations.scenePlanGuideContinue",
    }),
  );
  expect(select).toHaveBeenCalledWith(worker);
});
it("filters wrong organization, assignment and inactive workers", () => {
  render(
    <ScenePlanGuide
      scene={scene}
      workers={[
        { ...worker, organization_id: "other" },
        { ...worker, agent_id: "other" },
        { ...worker, status: "paused" } as DigitalWorker,
      ]}
      canManage
      onSelect={vi.fn()}
      onClose={() => {}}
    />,
  );
  expect(screen.getByText("organizations.scenePlanGuideNoWorker")).toBeTruthy();
  expect(
    screen.getByRole("button", {
      name: "organizations.scenePlanGuideContinue",
    }),
  ).toBeDisabled();
});
it("gives non-manager preparation guidance instead of a failed request", () => {
  render(
    <ScenePlanGuide
      scene={scene}
      workers={[]}
      canManage={false}
      onSelect={vi.fn()}
      onClose={() => {}}
    />,
  );
  expect(
    screen.getByText("organizations.scenePlanGuideAskManager"),
  ).toBeTruthy();
  expect(screen.getByText("organizations.scenePlanGuideReadOnly")).toBeTruthy();
});

it("does not continue from a disabled scene", async () => {
  const select = vi.fn();
  render(
    <ScenePlanGuide
      scene={{ ...scene, enabled: false }}
      workers={[worker]}
      canManage
      onSelect={select}
      onClose={() => {}}
    />,
  );
  fireEvent.mouseDown(screen.getByRole("combobox"));
  fireEvent.click(await screen.findByText("Worker"));
  expect(
    screen.getByRole("button", {
      name: "organizations.scenePlanGuideContinue",
    }),
  ).toBeDisabled();
  expect(select).not.toHaveBeenCalled();
});
