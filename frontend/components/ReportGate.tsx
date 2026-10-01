"use client";

/** Shows the report form to everyone except staff.

Reporting is a citizen action, so a signed-in department-desk or command-center
account is sent to its own workspace instead of being offered a form the API
would refuse. This is a usability choice, not the control: `reporter_only` on
the backend rejects a staff submission with a 403 regardless of what renders
here.

Anonymous visitors are the common case and must keep working — this gate only
ever redirects a user it has positively identified as staff. */

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import ReportForm from "@/components/ReportForm";
import { homeForRole, isStaffRole, useAuth } from "@/lib/auth";

export default function ReportGate() {
  const { user, loading } = useAuth();
  const router = useRouter();
  const isStaff = isStaffRole(user?.role);

  useEffect(() => {
    if (loading || !user || !isStaff) return;
    router.replace(homeForRole(user.role));
  }, [loading, user, isStaff, router]);

  // While the session resolves, render nothing rather than flashing a form at
  // a staff user and snatching it away a moment later.
  if (loading || isStaff) return null;

  return <ReportForm />;
}
