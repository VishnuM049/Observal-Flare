import type { SleepMode } from "./types";

const EC2_MONTHLY: Record<string, number> = {
  "t3.medium": 28.80,
  "t3.large": 57.60,
  "t3.xlarge": 180.00,
  "t3.2xlarge": 288.00,
};

const EBS_MONTHLY = 4;
const DATA_TRANSFER_MONTHLY = 2;
const EIP_STOPPED_MONTHLY = 3.6;

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
  idleTimeoutMinutes?: number
): number {
  const ec2 = EC2_MONTHLY[instanceSize] ?? EC2_MONTHLY["t3.large"];
  const fraction = runningFraction(sleepMode, sleepAtHour, wakeAtHour, idleTimeoutMinutes);
  const ec2Cost = (ec2 / 30) * fraction;
  const eipCost = fraction < 1 ? (EIP_STOPPED_MONTHLY / 30) * (1 - fraction) : 0;
  const fixedCost = (EBS_MONTHLY + DATA_TRANSFER_MONTHLY) / 30;
  return Math.round((ec2Cost + eipCost + fixedCost) * 100) / 100;
}

export function formatDailyCost(dollars: number): string {
  return `~$${dollars.toFixed(2)}/day`;
}
