/**
 * The readable text of an HTML fragment a repository sent, as plain text.
 *
 * Repository descriptions arrive as HTML (`<p>…</p>`). They are never inserted
 * as markup; the question is only how to show their words. A regex that strips
 * `<…>` is the incomplete sanitiser CodeQL flags (#29) — nested or broken tags
 * survive it — and dropping it showed researchers the raw tags instead.
 *
 * `DOMParser` with `text/html` builds an inert document: scripts do not run and
 * nothing is fetched. Only `textContent` leaves it, and React escapes that when
 * it renders, so no markup from a repository ever reaches the page.
 */
export function plainText(html: string): string {
  if (!html) return "";
  if (typeof DOMParser === "undefined") {
    // The server render: say nothing rather than the raw markup. The browser
    // fills it in on mount — see the component that calls this.
    return "";
  }
  const text = new DOMParser().parseFromString(html, "text/html").body.textContent ?? "";
  return text.replace(/\s+/g, " ").trim();
}
