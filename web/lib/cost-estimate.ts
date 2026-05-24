import type { CloudProvider, SleepMode } from "./types";

// AWS pricing — update periodically from https://aws.amazon.com/ec2/pricing/
const EC2_MONTHLY: Record<string, number> = {
  "t3.medium": 28.80,
  "t3.large": 57.60,
  "t3.xlarge": 180.00,
  "t3.2xlarge": 288.00,
};
const EBS_MONTHLY = 4;
const DATA_TRANSFER_MONTHLY = 2;
const EIP_STOPPED_MONTHLY = 3.6;

// GCP pricing — update periodically from https://cloud.google.com/compute/pricing
const GCE_MONTHLY: Record<string, number> = {
  "e2-medium": 24.27,
  "e2-standard-2": 48.54,
  "e2-standard-4": 97.09,
  "e2-standard-8": 194.18,
};
const GCE_DISK_MONTHLY = 3.40;
const GCE_DATA_TRANSFER_MONTHLY = 1.50;
const GCE_STATIC_IP_STOPPED_MONTHLY = 2.88;

function runningFraction(
  sleepMode: SleepMode,
  sleepAtHour?: number,
  wakeAtHour?: number,
  idleTimeoutMinutes?: number
): number {
  if (sleepMode === "nightly") {
    const wake = wakeAtHour ?? 7;
    const sleep = sleepAtHour ?? 19;
    let hours: number;
    if (sleep > wake) {
      hours = sleep - wake;
    } else if (sleep < wake) {
      hours = 24 - wake + sleep;
    } else {
      hours = 24;
    }
    return hours / 24;
  }
  if (sleepMode === "idle") {
    return 0.42;
  }
  return 1.0;
}

export function estimateDailyCost(
  instanceSize: string,
  sleepMode: SleepMode,
  sleepAtHour?: number,
  wakeAtHour?: number,
  idleTimeoutMinutes?: number,
  cloudProvider: CloudProvider = "aws"
): number {
  let computeMonthly: number;
  let diskMonthly: number;
  let dataTransferMonthly: number;
  let staticIpStoppedMonthly: number;

  if (cloudProvider === "gcp") {
    computeMonthly = GCE_MONTHLY[instanceSize] ?? GCE_MONTHLY["e2-standard-2"];
    diskMonthly = GCE_DISK_MONTHLY;
    dataTransferMonthly = GCE_DATA_TRANSFER_MONTHLY;
    staticIpStoppedMonthly = GCE_STATIC_IP_STOPPED_MONTHLY;
  } else {
    computeMonthly = EC2_MONTHLY[instanceSize] ?? EC2_MONTHLY["t3.large"];
    diskMonthly = EBS_MONTHLY;
    dataTransferMonthly = DATA_TRANSFER_MONTHLY;
    staticIpStoppedMonthly = EIP_STOPPED_MONTHLY;
  }

  const fraction = runningFraction(sleepMode, sleepAtHour, wakeAtHour, idleTimeoutMinutes);
  const computeCost = (computeMonthly / 30) * fraction;
  const ipCost = fraction < 1 ? (staticIpStoppedMonthly / 30) * (1 - fraction) : 0;
  const fixedCost = (diskMonthly + dataTransferMonthly) / 30;
  return Math.round((computeCost + ipCost + fixedCost) * 100) / 100;
}

export function formatDailyCost(dollars: number): string {
  return `~$${dollars.toFixed(2)}/day`;
}
