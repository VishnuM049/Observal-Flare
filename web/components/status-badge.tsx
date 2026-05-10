import type { SiteStatus } from "@/lib/types";

const STATUS_STYLES: Record<SiteStatus, string> = {
  pending: "bg-gray-100 text-gray-700",
  provisioning: "bg-blue-100 text-blue-700",
  deploying: "bg-blue-100 text-blue-700",
  running: "bg-green-100 text-green-700",
  stopping: "bg-yellow-100 text-yellow-700",
  stopped: "bg-gray-100 text-gray-700",
  sleeping: "bg-purple-100 text-purple-700",
  destroying: "bg-red-100 text-red-700",
  destroyed: "bg-gray-100 text-gray-400",
  failed: "bg-red-100 text-red-700",
};

export function StatusBadge({ status }: { status: SiteStatus }) {
  return (
    <span
      className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${STATUS_STYLES[status]}`}
    >
      {status}
    </span>
  );
}
