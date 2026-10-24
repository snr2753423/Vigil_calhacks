import { Loader2 } from "lucide-react";
import { notFound } from "next/navigation";
import { Suspense } from "react";

import { BedSensor } from "@/components/bed/bed-sensor";

const RESIDENT_IDS = Array.from({ length: 120 }, (_, i) => String(i + 1));

export function generateStaticParams() {
  return RESIDENT_IDS.map((residentId) => ({ residentId }));
}

async function Sensor({ params }: { params: PageProps<"/bed/[residentId]">["params"] }) {
  const { residentId } = await params;
  if (!RESIDENT_IDS.includes(residentId)) notFound();
  return <BedSensor residentId={Number(residentId)} />;
}

/** The iPhone bed-sensor page (SPEC §11.6), opened from the QR code on /devices over HTTPS. */
export default function BedPage({ params }: PageProps<"/bed/[residentId]">) {
  return (
    <Suspense
      fallback={
        <main className="flex flex-1 items-center justify-center gap-3 p-8 text-lg text-muted-foreground">
          <Loader2 className="size-6 animate-spin" aria-hidden /> Loading…
        </main>
      }
    >
      <Sensor params={params} />
    </Suspense>
  );
}
