import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import InspectionRunOptions, {
  emptyInspectionRun,
  inspectionRunMessages,
  type InspectionRunValue,
} from "./InspectionRunOptions";
import {
  inspectionRunApi,
  type InspectionJobOption,
} from "../../api/modules/inspectionRun";
vi.mock("../../api/modules/inspectionRun", () => ({
  inspectionRunApi: { candidates: vi.fn() },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ i18n: { language: "en" } }),
}));
const m = inspectionRunMessages.en;
function Fixture({ scene = "scene" }: { scene?: string }) {
  const [value, setValue] = useState<InspectionRunValue>(emptyInspectionRun);
  return (
    <>
      <InspectionRunOptions
        key={scene}
        organizationId="org"
        sceneId={scene}
        value={value}
        onChange={setValue}
      />
      <output data-testid="value">{JSON.stringify(value)}</output>
    </>
  );
}
const job = {
  job_id: "job",
  name: "Application health",
  job_version: 2,
  connection_version: 3,
};
beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(inspectionRunApi.candidates).mockResolvedValue([job]);
});
afterEach(cleanup);
it("does not load until enabled, resets sharing on source changes and keeps ordinary mode empty", async () => {
  render(<Fixture />);
  expect(inspectionRunApi.candidates).not.toHaveBeenCalled();
  fireEvent.click(screen.getByLabelText(m.mode));
  await screen.findByText(/Application health/);
  expect(inspectionRunApi.candidates).toHaveBeenCalledWith("org", "scene");
  expect(screen.getByLabelText(m.share)).toBeDisabled();
  fireEvent.click(screen.getByLabelText(/Application health/));
  fireEvent.click(screen.getByLabelText(m.share));
  expect(screen.getByTestId("value")).toHaveTextContent('"share":true');
  fireEvent.click(screen.getByLabelText(/Application health/));
  expect(screen.getByTestId("value")).toHaveTextContent('"share":false');
  fireEvent.click(screen.getByLabelText(m.mode));
  expect(screen.getByTestId("value")).toHaveTextContent(
    JSON.stringify(emptyInspectionRun),
  );
});
it("shows safe error without server text, retries explicitly and does not send any run", async () => {
  vi.mocked(inspectionRunApi.candidates).mockRejectedValueOnce(
    new Error("PRIVATE token"),
  );
  render(<Fixture />);
  fireEvent.click(screen.getByLabelText(m.mode));
  await screen.findByText(m.failed);
  expect(screen.queryByText(/PRIVATE/)).toBeNull();
  expect(screen.getByLabelText(m.share)).toBeDisabled();
  fireEvent.click(screen.getByText(m.retry));
  await screen.findByText(/Application health/);
});
it("drops late responses after disabling and after changing scene", async () => {
  let resolve!: (jobs: InspectionJobOption[]) => void;
  vi.mocked(inspectionRunApi.candidates).mockReturnValue(
    new Promise((r) => {
      resolve = r;
    }),
  );
  const page = render(<Fixture />);
  fireEvent.click(screen.getByLabelText(m.mode));
  page.rerender(<Fixture scene="other" />);
  await act(async () => resolve([job]));
  expect(screen.queryByText(/Application health/)).toBeNull();
  fireEvent.click(screen.getByLabelText(m.mode)); // clear inherited enabled flag
  expect(screen.getByTestId("value")).toHaveTextContent('"enabled":false');
});
it("limits selection to ten sources", async () => {
  vi.mocked(inspectionRunApi.candidates).mockResolvedValue(
    Array.from({ length: 11 }, (_, i) => ({
      ...job,
      job_id: `job${i}`,
      name: `Source ${i}`,
    })),
  );
  render(<Fixture />);
  fireEvent.click(screen.getByLabelText(m.mode));
  await screen.findByText(/Source 0 /);
  for (let i = 0; i < 10; i++)
    fireEvent.click(screen.getByLabelText(new RegExp(`Source ${i} `)));
  expect(screen.getByLabelText(/Source 10 /)).toBeDisabled();
  await waitFor(() =>
    expect(screen.getByLabelText(m.share)).not.toBeDisabled(),
  );
});
