import type { Metadata } from "next";

import { JobClient } from "@/components/JobClient";

export const dynamic = "force-dynamic";

export async function generateMetadata({
  params,
}: {
  params: Promise<{ id: string }>;
}): Promise<Metadata> {
  const { id } = await params;
  return { title: `Job ${id.slice(0, 8)}` };
}

export default async function JobPage({ params }: { params: Promise<{ id: string }> }) {
  // Next 15+ passes route params as a promise; awaiting it in the server
  // component keeps this page compatible with both static and dynamic rendering.
  const { id } = await params;
  return <JobClient jobId={id} />;
}
