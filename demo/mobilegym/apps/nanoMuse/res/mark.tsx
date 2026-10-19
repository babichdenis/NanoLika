import { useId, type CSSProperties } from 'react';

/**
 * The nanoMuse mark — the one-stroke N in the brand gradient — as the launcher icon of the
 * simulated phone: the same icon the Android app has (scripts/gen-android-icons.py draws it
 * from assets/brand/nanomuse-mark.svg onto a white tile; this is that path). It takes the
 * props the launcher passes a lucide icon and keeps its own colours.
 */
const N =
  'M49 71 C18 67 9 28 18 20 C38 17 52 47 49 71 Z M51 69 C48 40 68 12 84 19 C94 38 73 64 51 69 Z M46 73 C25 84 13 71 18 59 C29 52 42 60 46 73 Z M54 73 C74 87 89 74 83 61 C70 54 60 63 54 73 Z';

export function NanoMuseMark({
  size = 48,
  className,
  style,
}: {
  size?: number | string;
  color?: string;
  strokeWidth?: number | string;
  className?: string;
  style?: CSSProperties;
}) {
  // one gradient id per instance: several marks on one page must not share a <defs> entry
  const id = `nm-mark-${useId().replace(/:/g, '')}`;
  return (
    <svg viewBox="8 16 84 68" width={size} height={size} className={className} style={style} aria-hidden="true">
      <defs>
        <linearGradient id={id} gradientUnits="userSpaceOnUse" x1="12.6" y1="50" x2="87.8" y2="50">
          <stop offset="0" stopColor="#52694e" />
          <stop offset="1" stopColor="#a8bfa4" />
        </linearGradient>
      </defs>
      <path d={N} fill={`url(#${id})`} />
    </svg>
  );
}
