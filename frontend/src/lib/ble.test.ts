import { describe, expect, it } from "vitest";

import { bleReconnectDelayMs, ingestMessage, parseHeartRateMeasurement, wornFrom } from "@/lib/ble";

const view = (...bytes: number[]) => new DataView(new Uint8Array(bytes).buffer);

describe("parseHeartRateMeasurement", () => {
  it("reads an 8-bit heart rate", () => {
    const m = parseHeartRateMeasurement(view(0x00, 72));
    expect(m).toEqual({ hr: 72, contactSupported: false, contact: null, energyExpendedKJ: null, rrIntervalsMs: [] });
  });

  it("reads a 16-bit little-endian heart rate", () => {
    expect(parseHeartRateMeasurement(view(0x01, 0x2c, 0x01)).hr).toBe(300);
    expect(parseHeartRateMeasurement(view(0x01, 0x48, 0x00)).hr).toBe(72);
  });

  it("decodes the sensor-contact bits", () => {
    // bit 2 supported + bit 1 detected
    expect(parseHeartRateMeasurement(view(0x06, 64))).toMatchObject({ contactSupported: true, contact: true });
    // supported, not detected (band off the skin)
    expect(parseHeartRateMeasurement(view(0x04, 0))).toMatchObject({ contactSupported: true, contact: false });
    // bit 1 without bit 2 means nothing
    expect(parseHeartRateMeasurement(view(0x02, 64))).toMatchObject({ contactSupported: false, contact: null });
  });

  it("reads RR intervals in 1/1024 s after an 8-bit HR", () => {
    // 0x10 RR present: 1024 → 1000 ms, 820 → 801 ms
    const m = parseHeartRateMeasurement(view(0x16, 75, 0x00, 0x04, 0x34, 0x03));
    expect(m.hr).toBe(75);
    expect(m.contact).toBe(true);
    expect(m.rrIntervalsMs).toEqual([1000, 801]);
  });

  it("skips energy expended before RR intervals with a 16-bit HR", () => {
    // 16-bit HR + energy (0x08) + RR (0x10)
    const m = parseHeartRateMeasurement(view(0x19, 0x50, 0x00, 0x10, 0x00, 0x00, 0x02));
    expect(m).toMatchObject({ hr: 80, energyExpendedKJ: 16, rrIntervalsMs: [500] });
  });

  it("ignores a trailing odd byte and rejects truncated values", () => {
    expect(parseHeartRateMeasurement(view(0x10, 60, 0x00, 0x04, 0x99)).rrIntervalsMs).toEqual([1000]);
    expect(() => parseHeartRateMeasurement(view(0x00))).toThrow();
    expect(() => parseHeartRateMeasurement(view(0x01, 0x48))).toThrow();
  });
});

describe("worn and the ingest message", () => {
  it("uses the contact bit when supported, else the presence of a reading", () => {
    expect(wornFrom(parseHeartRateMeasurement(view(0x04, 70)))).toBe(false);
    expect(wornFrom(parseHeartRateMeasurement(view(0x06, 70)))).toBe(true);
    expect(wornFrom(parseHeartRateMeasurement(view(0x00, 70)))).toBe(true);
    expect(wornFrom(parseHeartRateMeasurement(view(0x00, 0)))).toBeNull();
  });

  it("sends HR 0 as null with the resident and timestamp", () => {
    expect(ingestMessage(parseHeartRateMeasurement(view(0x04, 0)), 120, 1000)).toEqual({
      resident_id: 120,
      ts: 1000,
      hr: null,
      worn: false,
    });
    expect(ingestMessage(parseHeartRateMeasurement(view(0x06, 66)), 120, 1000).hr).toBe(66);
  });

  it("backs off reconnects up to 15 s", () => {
    expect([0, 1, 2, 3, 4, 10].map(bleReconnectDelayMs)).toEqual([1000, 2000, 4000, 8000, 15000, 15000]);
  });
});
