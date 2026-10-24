import { describe, expect, it } from "vitest";

import { clockTheme, effectiveTheme, toggleTheme } from "./theme";

describe("day / night theme", () => {
  it("follows the facility clock: night 21:00–07:00", () => {
    expect(clockTheme(20)).toBe("day");
    expect(clockTheme(21)).toBe("night");
    expect(clockTheme(2)).toBe("night");
    expect(clockTheme(6)).toBe("night");
    expect(clockTheme(7)).toBe("day");
  });

  it("forced night and replays of last night are night", () => {
    expect(clockTheme(14, true)).toBe("night");
    expect(clockTheme(14, false, true)).toBe("night");
  });

  it("a manual pick wins; toggling back to the clock's pick returns to auto", () => {
    expect(effectiveTheme("day", "night")).toBe("day");
    expect(effectiveTheme("auto", "night")).toBe("night");
    expect(toggleTheme("auto", "night")).toBe("day");
    expect(toggleTheme("day", "night")).toBe("auto");
    expect(toggleTheme("auto", "day")).toBe("night");
  });
});
