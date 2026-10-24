// Main navigation (SPEC §11). Every role can reach every page; the role only decides which links sit in the header
// and which go under "More" (on phones everything is in one menu).
import type { Role } from "@/store/prefs";

export type NavItem = { href: string; label: string; hint: string };

export const NAV_ITEMS: NavItem[] = [
  { href: "/", label: "Rounds", hint: "Who to check next and why" },
  { href: "/handoff", label: "Handoff", hint: "Shift handoff (SBAR)" },
  { href: "/ask", label: "Ask", hint: "Ask the facility" },
  { href: "/devices", label: "Devices", hint: "Polar band, iPhone bed sensor" },
  { href: "/under-the-hood", label: "Under the hood", hint: "Tiger Data + Snowflake proof" },
  { href: "/admin", label: "Admin", hint: "Replay, night mode, injectors" },
];

const PRIMARY: Record<Role, string[]> = {
  cna: ["/", "/devices"], // rounds on a tablet or phone; pairs the band
  rn: ["/", "/handoff", "/ask"], // escalation, resident detail, shift handoff
  don: ["/", "/ask", "/handoff", "/under-the-hood"], // facility analytics
};

/** The header links for a role (in their order) and the rest, in NAV_ITEMS order. */
export function navFor(role: Role): { primary: NavItem[]; more: NavItem[] } {
  const hrefs = PRIMARY[role];
  const byHref = new Map(NAV_ITEMS.map((n) => [n.href, n]));
  return {
    primary: hrefs.map((h) => byHref.get(h)).filter((n): n is NavItem => n !== undefined),
    more: NAV_ITEMS.filter((n) => !hrefs.includes(n.href)),
  };
}

/** Is `href` the current section? The resident pages belong to Rounds. */
export function isActive(href: string, path: string): boolean {
  return href === "/" ? path === "/" || path.startsWith("/resident") : path.startsWith(href);
}
