import type { StatusChange } from "../api/types";
import { formatDateTime, STATUS_LABEL } from "../lib/format";

export function History({ changes }: { changes: StatusChange[] }) {
  return (
    <ol className="relative border-l border-line pl-5">
      {changes.map((change, index) => (
        <li key={index} className="pb-4 last:pb-0">
          <span className="absolute -left-[5px] mt-1.5 h-2.5 w-2.5 rounded-sm bg-cobalt" aria-hidden="true" />
          <p className="text-sm font-semibold">{STATUS_LABEL[change.to_status]}</p>
          <p className="text-sm text-ink-muted">
            {change.reason} <span className="text-ink-faint">({formatDateTime(change.created_at)})</span>
          </p>
        </li>
      ))}
    </ol>
  );
}
