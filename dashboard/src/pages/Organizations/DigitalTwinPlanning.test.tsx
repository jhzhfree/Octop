import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import DigitalTwinPlanning from "./DigitalTwinPlanning";

function setup() {
  const onDraft = vi.fn();
  render(
    <DigitalTwinPlanning
      goalTitle="Research competitors"
      successCriteria="Three cited sources"
      onDraft={onDraft}
    />,
  );
  return { user: userEvent.setup(), onDraft };
}

describe("DigitalTwinPlanning", () => {
  it("shows the existing goal and generates only after required answers", async () => {
    const { user, onDraft } = setup();
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.open" }),
    );
    expect(screen.getByText("Research competitors")).toBeInTheDocument();
    expect(screen.getByText("Three cited sources")).toBeInTheDocument();
    expect(
      screen.getByText("digitalTwinPlanning.draftOnly"),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.continue" }),
    );
    expect(onDraft).not.toHaveBeenCalled();
    await user.type(
      screen.getByLabelText("digitalTwinPlanning.sources"),
      "Source list",
    );
    await user.type(
      screen.getByLabelText("digitalTwinPlanning.deliverable"),
      "Report",
    );
    await user.type(
      screen.getByLabelText("digitalTwinPlanning.reviewer"),
      "Manager",
    );
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.continue" }),
    );
    await waitFor(() => expect(onDraft).toHaveBeenCalledTimes(1));
    expect(onDraft.mock.calls[0][0]).toMatchObject({
      title: "Research competitors",
    });
    expect(onDraft.mock.calls[0][0].steps).toHaveLength(5);
  });

  it("cancels without creating a draft and clears answers on reopening", async () => {
    const { user, onDraft } = setup();
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.open" }),
    );
    await user.type(
      screen.getByLabelText("digitalTwinPlanning.sources"),
      "Discard me",
    );
    await user.click(screen.getByRole("button", { name: "Close" }));
    expect(onDraft).not.toHaveBeenCalled();
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.open" }),
    );
    expect(screen.getByLabelText("digitalTwinPlanning.sources")).toHaveValue(
      "",
    );
  });

  it("rejects whitespace-only required answers", async () => {
    const { user, onDraft } = setup();
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.open" }),
    );
    for (const field of ["sources", "deliverable", "reviewer"]) {
      await user.type(
        screen.getByLabelText(`digitalTwinPlanning.${field}`),
        "   ",
      );
    }
    await user.click(
      screen.getByRole("button", { name: "digitalTwinPlanning.continue" }),
    );
    expect(onDraft).not.toHaveBeenCalled();
  });
});
