import { describe, expect, it } from "vitest";

import { actionFor, ignoreKey, nextSpeed } from "./shortcuts";

const key = (k: string, mods: Partial<KeyboardEvent> = {}) =>
  ({ key: k, shiftKey: false, metaKey: false, ctrlKey: false, altKey: false, ...mods }) as KeyboardEvent;

describe("demo shortcuts", () => {
  it("maps keys to actions", () => {
    expect(actionFor(key("?", { shiftKey: true }))).toEqual({ kind: "toggle_panel" });
    expect(actionFor(key(" "))).toEqual({ kind: "play_pause" });
    expect(actionFor(key("3"))).toEqual({ kind: "bookmark", index: 2 });
    expect(actionFor(key("0"))).toBeNull();
    expect(actionFor(key("n"))).toEqual({ kind: "force_night" });
    expect(actionFor(key("b"))).toEqual({ kind: "bed_exit" });
    expect(actionFor(key("B", { shiftKey: true }))).toEqual({ kind: "bed_return" });
    expect(actionFor(key("l"))).toEqual({ kind: "live" });
    expect(actionFor(key("x"))).toBeNull();
  });

  it("g-prefixed keys navigate", () => {
    expect(actionFor(key("r"), true)).toEqual({ kind: "goto", href: "/resident/120" });
    expect(actionFor(key("u"), true)).toEqual({ kind: "goto", href: "/under-the-hood" });
    expect(actionFor(key("z"), true)).toBeNull();
  });

  it("ignores typing and browser shortcuts", () => {
    const input = { tagName: "INPUT" } as unknown as EventTarget;
    const div = { tagName: "DIV", isContentEditable: false } as unknown as EventTarget;
    const editable = { tagName: "DIV", isContentEditable: true } as unknown as EventTarget;
    expect(ignoreKey(key("n"), input)).toBe(true);
    expect(ignoreKey(key("n"), editable)).toBe(true);
    expect(ignoreKey(key("n"), div)).toBe(false);
    expect(ignoreKey(key("r", { metaKey: true }), div)).toBe(true);
  });

  it("cycles speeds", () => {
    expect(nextSpeed(30, [30, 60, 120])).toBe(60);
    expect(nextSpeed(120, [30, 60, 120])).toBe(30);
    expect(nextSpeed(undefined, [30, 60, 120])).toBe(30);
  });
});
