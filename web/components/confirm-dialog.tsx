"use client";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel?: string;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel?: () => void;
}

export function ConfirmDialog({
  open,
  title,
  message,
  confirmLabel = "Confirm",
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center">
      <div className="absolute inset-0 bg-black/40" onClick={onCancel || undefined} />
      <div className="relative card p-6 max-w-md w-full mx-4 shadow-lg" style={{ backgroundColor: "white" }}>
        <h3 className="text-lg font-bold mb-2">{title}</h3>
        <p className="text-sm mb-6" style={{ color: "var(--color-ink-light)" }}>{message}</p>
        <div className="flex gap-3 justify-end">
          <button onClick={onCancel || undefined} disabled={!onCancel} className="btn-secondary">
            Cancel
          </button>
          <button onClick={onConfirm} disabled={confirmDisabled} className="btn-danger">
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
