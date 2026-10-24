"use client";

import { ChevronDown, Menu, X } from "lucide-react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { cn } from "cn";

import { isActive, navFor, type NavItem } from "@/lib/nav";
import { ROLES, usePrefs } from "@/store/prefs";

/** Role-aware main nav: the role's pages inline plus "More" (md and up); one "Menu" button on phones. */
export function NavMenu() {
  const role = usePrefs((s) => s.role);
  const { primary, more } = navFor(role);
  return (
    <>
      <nav className="hidden items-center gap-1 md:flex" aria-label="Main">
        {primary.map((n) => (
          <InlineLink key={n.href} item={n} />
        ))}
        <Dropdown label="More" items={more} />
      </nav>
      <div className="md:hidden">
        <Dropdown label="Menu" items={[...primary, ...more]} split={primary.length} roleLabel={ROLES[role].short} phone />
      </div>
    </>
  );
}

function InlineLink({ item }: { item: NavItem }) {
  const path = usePathname();
  const active = isActive(item.href, path);
  return (
    <Link
      href={item.href}
      aria-current={active ? "page" : undefined}
      className={cn(
        "flex h-10 items-center rounded-lg px-3 text-base font-medium",
        active ? "bg-white/10 text-foreground" : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
      )}
    >
      {item.label}
    </Link>
  );
}

function Dropdown({ label, items, split, roleLabel, phone = false }: {
  label: string;
  items: NavItem[];
  split?: number; // phones: the first `split` items are the role's own pages
  roleLabel?: string;
  phone?: boolean;
}) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  const [openedOn, setOpenedOn] = useState(path);
  const box = useRef<HTMLDivElement>(null);
  if (open && openedOn !== path) setOpen(false); // navigated: close (render-time reset, no effect needed)

  useEffect(() => {
    if (!open) return;
    const onDown = (e: PointerEvent) => {
      if (box.current && !box.current.contains(e.target as Node)) setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const current = items.find((n) => isActive(n.href, path));
  return (
    <div ref={box} className="relative">
      <button
        type="button"
        aria-expanded={open}
        aria-haspopup="true"
        onClick={() => {
          setOpenedOn(path);
          setOpen(!open);
        }}
        className={cn(
          "flex h-10 items-center gap-1.5 rounded-lg px-3 text-base font-medium",
          current && !phone ? "bg-white/10 text-foreground" : "text-muted-foreground hover:bg-white/5 hover:text-foreground",
          phone && "ring-1 ring-input",
        )}
      >
        {phone && (open ? <X className="size-5" aria-hidden /> : <Menu className="size-5" aria-hidden />)}
        {phone && current ? current.label : label}
        {!phone && <ChevronDown className={cn("size-4 transition-transform", open && "rotate-180")} aria-hidden />}
      </button>
      {open && (
        <ul
          className={cn(
            "absolute z-50 mt-1 min-w-64 rounded-xl border border-border/60 bg-popover p-1.5 shadow-xl",
            phone ? "left-0" : "right-0",
          )}
        >
          {items.map((n, i) => (
            <li key={n.href}>
              {phone && split !== undefined && (i === 0 || i === split) && (
                <p className="px-3 pt-2 pb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  {i === 0 ? `For ${roleLabel}` : "More"}
                </p>
              )}
              <Link
                href={n.href}
                aria-current={isActive(n.href, path) ? "page" : undefined}
                onClick={() => setOpen(false)}
                className={cn(
                  "flex min-h-12 flex-col justify-center rounded-lg px-3 py-1.5",
                  isActive(n.href, path) ? "bg-white/10" : "hover:bg-white/5",
                )}
              >
                <span className="text-base font-medium">{n.label}</span>
                <span className="text-sm text-muted-foreground">{n.hint}</span>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
