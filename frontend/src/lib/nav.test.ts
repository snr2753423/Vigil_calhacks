import { describe, expect, it } from "vitest";

import { isActive, NAV_ITEMS, navFor } from "./nav";

describe("role-aware nav", () => {
  it("every role reaches every page exactly once", () => {
    for (const role of ["cna", "rn", "don"] as const) {
      const { primary, more } = navFor(role);
      expect([...primary, ...more].map((n) => n.href).sort()).toEqual(NAV_ITEMS.map((n) => n.href).sort());
      expect(primary[0].href).toBe("/");
    }
  });

  it("puts each role's pages in the header", () => {
    expect(navFor("cna").primary.map((n) => n.label)).toEqual(["Rounds", "Devices"]);
    expect(navFor("don").primary.map((n) => n.label)).toContain("Ask");
  });

  it("resident pages belong to Rounds", () => {
    expect(isActive("/", "/resident/17")).toBe(true);
    expect(isActive("/", "/ask")).toBe(false);
    expect(isActive("/ask", "/ask")).toBe(true);
  });
});
