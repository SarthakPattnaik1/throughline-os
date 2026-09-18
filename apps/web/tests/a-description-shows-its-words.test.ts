/**
 * A repository's description shows its words, never its markup (CodeQL #29).
 */

import { describe, expect, it } from "vitest";
import { plainText } from "@/lib/plain-text";

describe("plainText", () => {
  it("keeps the words of an HTML description", () => {
    expect(plainText("<p>Panel of <b>national</b> resistance rates.</p>"))
      .toBe("Panel of national resistance rates.");
  });

  it("leaves nothing executable and nothing tag-shaped behind", () => {
    const text = plainText('<img src=x onerror="alert(1)"><scr<script>ipt>alert(2)</script>ok');
    expect(text).not.toMatch(/<|onerror/);
    expect(text).toContain("ok");
  });

  it("keeps text that merely looks like a comparison", () => {
    expect(plainText("n &lt; 30 and p &gt; 0.05")).toBe("n < 30 and p > 0.05");
  });
});
