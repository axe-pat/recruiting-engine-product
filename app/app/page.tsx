import type { Metadata } from "next";

import { AppFrame } from "@/components/AppFrame";

export const metadata: Metadata = {
  title: "Command center",
  description: "The local-first Recruiting Engine command center.",
};

export default function AppPage() {
  return <AppFrame view="dashboard" />;
}
