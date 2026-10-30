/**
 * A small azulejo-style tile pattern, generated from a seed. Stands in for listing photos
 * (we don't ship stock images) and gives every listing a recognisable mark.
 */
const PALETTE = ["#1F3A93", "#3A57B8", "#E7EDF9", "#FFFFFF"];

function hash(seed: string): number {
  let value = 2166136261;
  for (const char of seed) value = Math.imul(value ^ char.charCodeAt(0), 16777619);
  return value >>> 0;
}

export function TileArt({ seed, className }: { seed: string; className?: string }) {
  const base = hash(seed);
  const motif = base % 3;
  const rotate = (base >> 3) % 4;
  return (
    <svg viewBox="0 0 120 80" className={className} role="presentation" aria-hidden="true">
      <defs>
        <pattern id={`tile-${seed}`} width="20" height="20" patternUnits="userSpaceOnUse"
          patternTransform={`rotate(${rotate * 90} 10 10)`}>
          <rect width="20" height="20" fill={PALETTE[2]} />
          {motif === 0 && <path d="M0 0h10v10H0zM10 10h10v10H10z" fill={PALETTE[0]} />}
          {motif === 1 && <circle cx="10" cy="10" r="6" fill="none" stroke={PALETTE[0]} strokeWidth="3" />}
          {motif === 2 && <path d="M10 0l10 10-10 10L0 10z" fill={PALETTE[1]} />}
          <circle cx="10" cy="10" r="1.6" fill={PALETTE[3]} />
        </pattern>
      </defs>
      <rect width="120" height="80" fill={`url(#tile-${seed})`} />
    </svg>
  );
}
