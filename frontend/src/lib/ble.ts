// Bluetooth Heart Rate Measurement (GATT characteristic 0x2A37) parsing, pure so it is unit-tested (SPEC §10).
//
// Byte 0 is a flags field:
//   bit 0  HR value format: 0 = uint8 (byte 1), 1 = uint16 little-endian (bytes 1–2)
//   bit 1  sensor contact detected        } contact status is only meaningful
//   bit 2  sensor contact feature supported } when bit 2 is set
//   bit 3  energy expended present (uint16, kJ)
//   bit 4  RR intervals present (uint16 each, 1/1024 s), to the end of the value

export const HEART_RATE_SERVICE = "heart_rate";
export const HEART_RATE_MEASUREMENT = "heart_rate_measurement"; // 0x2A37
/** Polar Measurement Data service (ACC/PPI streams, SPEC §10 P2): requested so a later phase can use it. */
export const POLAR_PMD_SERVICE = "fb005c80-02e7-f387-1cad-8acd2d8df0c8";

export type HeartRateMeasurement = {
  /** Beats per minute; 0 when the band has no reading (e.g. off the wrist). */
  hr: number;
  contactSupported: boolean;
  /** Skin contact; null when the band doesn't report contact. */
  contact: boolean | null;
  energyExpendedKJ: number | null;
  /** Beat-to-beat intervals in milliseconds. */
  rrIntervalsMs: number[];
};

export function parseHeartRateMeasurement(data: DataView): HeartRateMeasurement {
  if (data.byteLength < 2) throw new Error("Heart rate measurement too short");
  const flags = data.getUint8(0);
  const hr16 = (flags & 0x01) !== 0;
  const contactSupported = (flags & 0x04) !== 0;
  const contactDetected = (flags & 0x02) !== 0;
  const hasEnergy = (flags & 0x08) !== 0;
  const hasRR = (flags & 0x10) !== 0;

  let offset = 1;
  if (hr16 && data.byteLength < 3) throw new Error("Heart rate measurement too short for a 16-bit value");
  const hr = hr16 ? data.getUint16(offset, true) : data.getUint8(offset);
  offset += hr16 ? 2 : 1;

  let energyExpendedKJ: number | null = null;
  if (hasEnergy && offset + 2 <= data.byteLength) {
    energyExpendedKJ = data.getUint16(offset, true);
    offset += 2;
  }

  const rrIntervalsMs: number[] = [];
  if (hasRR) {
    for (; offset + 2 <= data.byteLength; offset += 2) {
      rrIntervalsMs.push(Math.round((data.getUint16(offset, true) * 1000) / 1024));
    }
  }

  return {
    hr,
    contactSupported,
    contact: contactSupported ? contactDetected : null,
    energyExpendedKJ,
    rrIntervalsMs,
  };
}

/** The `worn` value sent to /ws/ingest: the contact bit when the band reports it, else "worn" while it
 *  produces a heart rate, else unknown. */
export function wornFrom(m: HeartRateMeasurement): boolean | null {
  if (m.contact !== null) return m.contact;
  return m.hr > 0 ? true : null;
}

/** The /ws/ingest message for one measurement (HR 0 = no reading, sent as null). */
export function ingestMessage(m: HeartRateMeasurement, residentId: number, now: number) {
  return { resident_id: residentId, ts: now, hr: m.hr > 0 ? m.hr : null, worn: wornFrom(m) };
}

/** Delay before the next auto-reconnect attempt to a band that dropped. */
export function bleReconnectDelayMs(attempt: number): number {
  return Math.min(1000 * 2 ** attempt, 15_000);
}
