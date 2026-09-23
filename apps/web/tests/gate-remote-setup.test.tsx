import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ApiError, api } from "@/lib/api";
import { Gate } from "@/components/Gate";

vi.mock("@/components/Sky", () => ({
  Sky: () => <div data-testid="sky" />,
}));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("remote first-run setup", () => {
  it("asks for the server bootstrap token after a network setup refusal", async () => {
    const user = userEvent.setup();
    vi.spyOn(api, "post")
      .mockRejectedValueOnce(new ApiError(
        403,
        "First-run setup reached Throughline over a network connection. "
          + "Enter the setup token printed by the Throughline server.",
      ))
      .mockResolvedValueOnce({ user: { id: "usr_1" } } as never);

    render(
      <Gate
        status={{ needs_setup: true, authenticated: false, user: null }}
        onDone={vi.fn()}
      />,
    );

    await user.type(screen.getByLabelText("Name"), "Researcher");
    await user.type(screen.getByLabelText("Email"), "r@example.test");
    await user.type(screen.getByLabelText("Password"), "correct horse battery");
    await user.click(screen.getByRole("button", { name: /create account and continue/i }));

    const token = await screen.findByLabelText("Setup token");
    expect(token).toBeTruthy();

    await user.type(token, "operator-token");
    await user.click(screen.getByRole("button", { name: /create account and continue/i }));

    expect(api.post).toHaveBeenLastCalledWith("/api/auth/setup", {
      email: "r@example.test",
      display_name: "Researcher",
      password: "correct horse battery",
      setup_token: "operator-token",
    });
  });
});
