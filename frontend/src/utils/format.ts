export function formatCents(value: number | null, digits = 1) {
  return value == null ? "--" : `${(value * 100).toFixed(digits)}¢`;
}

export function formatCompact(value: number | null) {
  return value === null ? "--" : value.toLocaleString(undefined, { maximumFractionDigits: 0 });
}
