import type { SleepMode } from "./types";

const EC2_MONTHLY: Record<string, number> = {
  "t3.medium": 30,
  "t3.large": 54,
  "t3.xlarge": 108,
};

const EBS_MONTHLY = 4;
const DATA_TRANSFER_MONTHLY = 2;
const EIP_STOPPED_MONTHLY = 3.6;

const SLEEP_RUNNING_FRACTION: Record<SleepMode, number> = {
  none: 1.0,
  nightly: 0.42,
  idle: 0.42,
};

export function estimateDailyCost(
  instanceSize: string,
  sleepMode: SleepMode
): number {
  const ec2 = EC2_MONTHLY[instanceSize] ?? EC2_MONTHLY["t3.large"];
  const fraction = SLEEP_RUNNING_FRACTION[sleepMode];
  const ec2Cost = ec2 * fraction;
  const eipCost = fraction < 1 ? EIP_STOPPED_MONTHLY * (1 - fraction) : 0;
  const monthly = ec2Cost + EBS_MONTHLY + DATA_TRANSFER_MONTHLY + eipCost;
  return Math.round((monthly / 30) * 100) / 100;
}

export function formatDailyCost(dollars: number): string {
  return `~$${dollars.toFixed(2)}/day`;
}
