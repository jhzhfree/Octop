import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  organizationsApi,
  type PlanningDialogueResponse,
} from "../../api/modules/organizations";
import PlanningDialogue from "./PlanningDialogue";

vi.mock("../../api/modules/organizations", () => ({
  organizationsApi: { planningDialogue: vi.fn() },
}));
const call = vi.mocked(organizationsApi.planningDialogue);
const response: PlanningDialogueResponse = {
  reply: "A draft for review",
  model_ref: "provider/model",
  draft: {
    title: "Report draft",
    approach: "Read approved sources",
    steps: [{ action: "Review", expected_output: "Cited report" }],
  },
};
function mount(onDraft = vi.fn()) {
  const result = render(
    <PlanningDialogue
      organizationId="org"
      workerId="worker"
      workerVersion={2}
      goalId="goal"
      goalTitle="Report goal"
      onDraft={onDraft}
    />,
  );
  fireEvent.click(
    screen.getByRole("button", { name: "planningDialogue.open" }),
  );
  return { ...result, onDraft };
}
function send(text = "Plan the report") {
  fireEvent.change(screen.getByLabelText("planningDialogue.input"), {
    target: { value: text },
  });
  fireEvent.click(
    screen.getByRole("button", { name: /planningDialogue\.send/ }),
  );
}
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

beforeEach(() => {
  call.mockReset();
});
describe("tool-free planning dialogue", () => {
  it("sends bounded context and only applies a draft after explicit confirmation", async () => {
    call.mockResolvedValue(response);
    const { onDraft } = mount();
    expect(screen.getByText("planningDialogue.disclosure")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /planningDialogue\.send/ }),
    ).toBeDisabled();
    send();
    await screen.findByText("Report draft");
    expect(onDraft).not.toHaveBeenCalled();
    expect(call).toHaveBeenCalledWith(
      "org",
      "worker",
      "goal",
      {
        worker_version: 2,
        messages: [{ role: "user", content: "Plan the report" }],
      },
      expect.any(AbortSignal),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "planningDialogue.useDraft" }),
    );
    expect(onDraft).toHaveBeenCalledOnce();
    expect(onDraft).toHaveBeenCalledWith(response.draft);
  });
  it("prevents duplicate sends while a request is pending", async () => {
    const pending = deferred<PlanningDialogueResponse>();
    call.mockReturnValue(pending.promise);
    mount();
    send();
    fireEvent.click(
      screen.getByRole("button", { name: /planningDialogue\.send/ }),
    );
    expect(call).toHaveBeenCalledOnce();
    await act(async () => {
      pending.resolve(response);
    });
  });
  it("shows errors without saving and allows retry", async () => {
    call
      .mockRejectedValueOnce(
        new Error('422 {"detail":{"code":"FAILED","message":"Try later"}}'),
      )
      .mockResolvedValueOnce(response);
    const { onDraft } = mount();
    send();
    await screen.findByText("Try later");
    expect(onDraft).not.toHaveBeenCalled();
    send();
    await screen.findByText("Report draft");
    expect(call).toHaveBeenCalledTimes(2);
  });
  it("aborts closing requests and ignores late responses after reopening", async () => {
    const pending = deferred<PlanningDialogueResponse>();
    call.mockReturnValue(pending.promise);
    mount();
    send();
    const signal = call.mock.calls[0][4];
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(signal?.aborted).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "planningDialogue.open" }),
    );
    await act(async () => {
      pending.resolve(response);
    });
    expect(screen.queryByText("Report draft")).not.toBeInTheDocument();
    expect(screen.getByLabelText("planningDialogue.input")).toHaveValue("");
  });
  it("stops after six user sends and does not accept extra messages", async () => {
    call.mockResolvedValue({ ...response, draft: null });
    mount();
    for (let i = 1; i <= 6; i++) {
      send(`Turn ${i}`);
      await waitFor(() => expect(call).toHaveBeenCalledTimes(i));
      await waitFor(() =>
        expect(screen.getByLabelText("planningDialogue.input")).toHaveValue(""),
      );
    }
    expect(screen.getByText("planningDialogue.limit")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /planningDialogue\.send/ }),
    ).toBeDisabled();
    expect(call.mock.calls[5][3].messages).toHaveLength(11);
  });
  it("preserves the last draft across clarifications and failures, then clears on close", async () => {
    call
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce({
        ...response,
        reply: "Which source?",
        draft: null,
      })
      .mockRejectedValueOnce(new Error("temporary failure"))
      .mockResolvedValueOnce(response)
      .mockResolvedValueOnce(response);
    mount();
    send();
    await screen.findByText("Report draft");
    send("Revise step 1");
    await screen.findByText("Which source?");
    expect(call.mock.calls[1][3].previous_draft).toEqual(response.draft);
    expect(screen.queryByText("Report draft")).not.toBeInTheDocument();
    send("Use internal source");
    await screen.findByText("temporary failure");
    send("Retry");
    await screen.findByText("Report draft");
    expect(call.mock.calls[3][3].previous_draft).toEqual(response.draft);
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    fireEvent.click(
      screen.getByRole("button", { name: "planningDialogue.open" }),
    );
    send("New conversation");
    await screen.findByText("Report draft");
    expect(call.mock.calls[4][3].previous_draft).toBeUndefined();
    expect(call.mock.calls[4][3].messages).toHaveLength(1);
  });
  it("aborts on unmount", async () => {
    const pending = deferred<PlanningDialogueResponse>();
    call.mockReturnValue(pending.promise);
    const { unmount } = mount();
    send();
    const signal = call.mock.calls[0][4];
    unmount();
    expect(signal?.aborted).toBe(true);
    await act(async () => {
      pending.resolve(response);
    });
  });
});
