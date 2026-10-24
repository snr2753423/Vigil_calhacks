import { Loader2 } from "lucide-react";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { ResidentView } from "@/components/resident/resident-view";

const RESIDENT_IDS = Array.from({ length: 120 }, (_, i) => String(i + 1));

// Residents 1–120 are known ahead of time, so every page is prerendered.
export function generateStaticParams() {
  return RESIDENT_IDS.map((id) => ({ id }));
}

async function Resident({ params }: { params: PageProps<"/resident/[id]">["params"] }) {
  const { id } = await params;
  if (!RESIDENT_IDS.includes(id)) notFound();
  return <ResidentView id={Number(id)} />;
}

export default function ResidentPage({ params }: PageProps<"/resident/[id]">) {
  // The param is read inside Suspense so client navigation from the queue shows this shell instantly.
  return (
    <Suspense
      fallback={
        <main className="flex flex-1 items-center justify-center gap-3 p-8 text-lg text-muted-foreground">
          <Loader2 className="size-6 animate-spin" aria-hidden /> Loading resident…
        </main>
      }
    >
      <Resident params={params} />
    </Suspense>
  );
}
