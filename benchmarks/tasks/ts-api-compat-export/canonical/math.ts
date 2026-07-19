export function multiply(value: number, factor: number): number {
  return value * factor;
}

export function times(value: number, factor = 2): number {
  return multiply(value, factor);
}
