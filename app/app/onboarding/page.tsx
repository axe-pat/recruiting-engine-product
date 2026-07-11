import type { Metadata } from "next";

import { OnboardingWizard } from "@/components/OnboardingWizard";

export const metadata: Metadata = {
  title: "Create your workspace",
  description: "Set up a private, portable Recruiting Engine workspace.",
};

export default function OnboardingPage() {
  return <OnboardingWizard />;
}
